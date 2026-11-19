package spirebot;

import basemod.BaseMod;
import basemod.interfaces.PostInitializeSubscriber;
import basemod.interfaces.PreUpdateSubscriber;
import com.evacipated.cardcrawl.modthespire.lib.ConfigUtils;
import com.evacipated.cardcrawl.modthespire.lib.SpireConfig;
import com.google.gson.JsonObject;
import com.megacrit.cardcrawl.core.Settings;
import communicationmod.CommandExecutor;
import communicationmod.CommunicationMod;
import communicationmod.GameStateConverter;
import communicationmod.GameStateListener;
import communicationmod.InvalidCommandException;
import communicationmod.OnStateChangeSubscriber;

import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.LinkedBlockingQueue;

/**
 * Starts the bot's bridge (the bundled Python) and connects it to the game through CommunicationMod's public API, in
 * place of CommunicationMod's own "command" setting:
 * - the game state CommunicationMod publishes at each change (GameStateConverter.getCommunicationState, the same JSON
 *   CommunicationMod would send its own program) goes to the bridge's standard input, one line each;
 * - each line the bridge prints is a command, executed as CommunicationMod executes its program's commands, one per
 *   frame before the game's update (CommandExecutor.executeCommand); an invalid one is answered as CommunicationMod
 *   answers it, {"error": ..., "ready_for_command": ...}. The bridge's first line is its "ready" signal.
 *
 * Why not CommunicationMod's "command": it splits that line at spaces, so a program in a folder whose path has spaces
 * cannot be started, and Steam Workshop folders have them (C:\Program Files (x86)\Steam\..., ~/Library/Application
 * Support/Steam/...). This process is started from an argument list, any path works. And both ends of the pipe are
 * UTF-8 (CommunicationMod writes in the system's default charset: on a Windows set to English the game's Chinese names
 * reached the bridge as question marks).
 *
 * The bridge is the "spire-bot" folder next to this jar (the Steam Workshop item: the jar on top, the bundle beside it),
 * else the folder named by bridge_dir in ModTheSpire's config for this mod (written by the zip package's install).
 * It is not started when CommunicationMod's own command is set (another program, or an older install of this bot):
 * two programs would send commands to one game. The panel then says why.
 */
public class BridgeProcess implements PostInitializeSubscriber, PreUpdateSubscriber, OnStateChangeSubscriber {
    static final String BUNDLE = "spire-bot";
    private static BridgeProcess instance = null;

    private Process process = null;
    private final BlockingQueue<String> toBridge = new LinkedBlockingQueue<>();
    private final ConcurrentLinkedQueue<String> fromBridge = new ConcurrentLinkedQueue<>();
    private volatile boolean ready = false;
    private volatile boolean readyHandled = false;

    public static void start() {
        instance = new BridgeProcess();
        BaseMod.subscribe(instance);
    }

    /** whether this jar started the bridge (else CommunicationMod's command, or nothing) */
    public static boolean running() {
        return instance != null && instance.process != null && instance.process.isAlive();
    }

    @Override
    public void receivePostInitialize() {
        String cmCommand = communicationModCommand();
        if (!cmCommand.isEmpty()) {
            System.out.println("spirebot: CommunicationMod starts its own program (" + cmCommand + "); the bundled bridge"
                    + " is not started");
            if (!cmCommand.contains("launch.py")) {
                status(zh() ? "Spire Bot 没有启动" : "Spire Bot is not started",
                        zh() ? "Communication Mod 设置里已经有一个要启动的程序，两个程序会同时给游戏发命令。"
                                + "要让 Spire Bot 打，请在 Communication Mod 的设置里把 command 清空，再重启游戏。"
                                : "Communication Mod is set to start another program, and two programs would both send "
                                + "commands to the game. To let Spire Bot play, clear the command in Communication "
                                + "Mod's settings and restart the game.");
            }
            return;
        }
        File root = bundle();
        if (root == null) {
            status(zh() ? "Spire Bot 没有启动" : "Spire Bot is not started",
                    zh() ? "找不到 Spire Bot 自带的程序（spire-bot 文件夹）。请重新订阅，或者重新解压安装包、再跑一次 install。"
                            : "Spire Bot's own program (the spire-bot folder) was not found. Subscribe again, or unzip "
                            + "the package again and run its install.");
            return;
        }
        File python = python(root);
        if (python == null || !python.isFile()) {
            status(zh() ? "Spire Bot 没有启动" : "Spire Bot is not started",
                    (zh() ? "这个系统没有 Spire Bot 自带的 Python：" : "No bundled Python for this system: ")
                            + (python == null ? System.getProperty("os.name") : python.getPath()));
            return;
        }
        if (!python.canExecute()) {
            python.setExecutable(true);  // (a download may not keep the execute bit)
        }
        File logs = logDir();
        logs.mkdirs();
        List<String> cmd = new ArrayList<>();
        cmd.add(python.getAbsolutePath());
        cmd.add("-I");
        cmd.add(new File(root, "launch.py").getAbsolutePath());
        ProcessBuilder pb = new ProcessBuilder(cmd);
        pb.directory(new File(".").getAbsoluteFile());  // the game's folder, as CommunicationMod starts its program
        Map<String, String> env = pb.environment();
        env.put("PYTHONUTF8", "1");
        env.put("PYTHONIOENCODING", "utf-8");
        env.put("SPIRE_BOT_ROOT", root.getAbsolutePath());
        if (!env.containsKey("CM_LOG_DIR") && !inBundle(logs, root)) {
            env.put("CM_LOG_DIR", logs.getAbsolutePath());
        }
        File settings = new File(configDir(), "spire-bot.toml");
        if (!env.containsKey("SPIRE_BOT_SETTINGS") && settings.isFile()) {
            env.put("SPIRE_BOT_SETTINGS", settings.getAbsolutePath());
        }
        pb.redirectError(ProcessBuilder.Redirect.appendTo(new File(logs, "bridge_stderr.log")));
        try {
            process = pb.start();
        } catch (Exception e) {
            System.out.println("spirebot: could not start the bridge: " + e);
            status(zh() ? "Spire Bot 没有启动" : "Spire Bot is not started",
                    (zh() ? "启动失败：" : "Could not start it: ") + e.getMessage());
            return;
        }
        System.out.println("spirebot: bridge started: " + cmd);
        CommunicationMod.subscribe(this);
        Thread reader = new Thread(this::readLoop, "spirebot-bridge-reader");
        reader.setDaemon(true);
        reader.start();
        Thread writer = new Thread(this::writeLoop, "spirebot-bridge-writer");
        writer.setDaemon(true);
        writer.start();
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            if (process != null && process.isAlive()) process.destroy();
        }));
    }

    private void readLoop() {
        try (BufferedReader r = new BufferedReader(new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) {
                if (!ready) {
                    ready = true;  // the first line is the ready signal (CommunicationMod takes any first line)
                    continue;
                }
                fromBridge.add(line);
            }
        } catch (Exception e) {
            System.out.println("spirebot: reading from the bridge: " + e);
        }
        System.out.println("spirebot: the bridge has ended");
    }

    private void writeLoop() {
        try (Writer w = new OutputStreamWriter(process.getOutputStream(), StandardCharsets.UTF_8)) {
            while (true) {
                String m = toBridge.take();
                w.write(m);
                w.write("\n");
                w.flush();
            }
        } catch (Exception e) {
            System.out.println("spirebot: writing to the bridge: " + e);
        }
    }

    /** One command per frame, before the game's update: as CommunicationMod.receivePreUpdate does with its program's. */
    @Override
    public void receivePreUpdate() {
        if (process == null) return;
        if (ready && !readyHandled) {
            readyHandled = true;
            if (GameStateListener.isWaitingForCommand()) {
                CommunicationMod.mustSendGameState = true;
            }
        }
        String m = fromBridge.poll();
        if (m == null) return;
        try {
            if (CommandExecutor.executeCommand(m)) {
                GameStateListener.registerCommandExecution();
            }
        } catch (InvalidCommandException e) {
            JsonObject o = new JsonObject();
            o.addProperty("error", e.getMessage());
            o.addProperty("ready_for_command", GameStateListener.isWaitingForCommand());
            toBridge.add(o.toString());
        }
    }

    @Override
    public void receiveOnStateChange() {
        if (process != null && process.isAlive() && readyHandled) {
            toBridge.add(GameStateConverter.getCommunicationState());
        }
    }

    // --- where things are

    static String communicationModCommand() {
        try {
            SpireConfig c = new SpireConfig("CommunicationMod", "config");
            String s = c.getString("command");
            return s == null ? "" : s.trim();
        } catch (Exception e) {
            return "";
        }
    }

    /** the bundle: "spire-bot" next to this jar, else the bridge_dir the zip install wrote */
    static File bundle() {
        try {
            File jar = new File(BridgeProcess.class.getProtectionDomain().getCodeSource().getLocation().toURI());
            File b = new File(jar.getParentFile(), BUNDLE);
            if (new File(b, "launch.py").isFile()) return b;
        } catch (Exception e) {
            System.out.println("spirebot: where is this jar: " + e);
        }
        try {
            SpireConfig c = new SpireConfig("spirebot", "config");
            String d = c.getString("bridge_dir");
            if (d != null && new File(d, "launch.py").isFile()) return new File(d);
        } catch (Exception e) {
            System.out.println("spirebot: reading bridge_dir: " + e);
        }
        return null;
    }

    static File python(File root) {
        String os = System.getProperty("os.name", "").toLowerCase();
        if (os.startsWith("windows")) {
            File f = new File(root, "runtime/win/python/python.exe");
            return f.isFile() ? f : new File(root, "runtime/python/python.exe");
        }
        if (os.startsWith("mac")) {
            File f = new File(root, "runtime/mac/python/bin/python3");
            return f.isFile() ? f : new File(root, "runtime/python/bin/python3");
        }
        return null;
    }

    /** this mod's folder in ModTheSpire's config (kept when the Workshop item is updated) */
    static File configDir() {
        return new File(ConfigUtils.CONFIG_DIR, "spirebot");
    }

    /** the logs: in the bundle when it is the zip package (as before), else in this mod's config folder */
    static File logDir() {
        File root = bundle();
        if (root != null && new File(root, "install.py").isFile()) return new File(root, "logs");
        return new File(configDir(), "logs");
    }

    private static boolean inBundle(File f, File root) {
        return f.getAbsolutePath().startsWith(root.getAbsolutePath());
    }

    private static boolean zh() {
        return Settings.language == Settings.GameLanguage.ZHS || Settings.language == Settings.GameLanguage.ZHT;
    }

    /** what the panel shows when the bridge could not be started (the bridge writes this file once it runs) */
    static void status(String title, String line) {
        try {
            JsonObject o = new JsonObject();
            o.addProperty("n", 1);
            o.addProperty("title", title);
            com.google.gson.JsonArray a = new com.google.gson.JsonArray();
            a.add(new com.google.gson.JsonPrimitive(line));
            o.add("lines", a);
            o.addProperty("hint", "");
            Files.write(Panel.STATUS.toPath(), o.toString().getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            System.out.println("spirebot: " + title + ": " + line);
        }
    }
}
