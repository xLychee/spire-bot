package spirebot;

import com.evacipated.cardcrawl.modthespire.lib.SpireInitializer;

import java.io.File;
import java.io.FileWriter;
import java.lang.management.ManagementFactory;

/**
 * Tells the bot's bridge that this game has loaded the mod: writes "&lt;process id&gt; &lt;unix seconds&gt;" into
 * spirebot_mod.loaded in the game's folder (the working directory), removed again when the game exits. The bridge
 * plays only when it finds it, because the simulator it follows assumes the facing rule of FaceTarget. Also starts the
 * in-game panel, and the bridge itself (BridgeProcess) when CommunicationMod does not start a program of its own.
 */
@SpireInitializer
public class SpireBotMod {
    public static void initialize() {
        try {
            File f = new File("spirebot_mod.loaded");
            String pid = ManagementFactory.getRuntimeMXBean().getName().split("@")[0];
            try (FileWriter w = new FileWriter(f)) {
                w.write(pid + " " + (System.currentTimeMillis() / 1000) + "\n");
            }
            f.deleteOnExit();
        } catch (Exception e) {
            System.out.println("spirebot: could not write spirebot_mod.loaded: " + e);
        }
        try {
            Names.start();
        } catch (Throwable e) {
            System.out.println("spirebot: the panel's names could not be set up: " + e);
        }
        try {
            Panel.start();
        } catch (Throwable e) {
            System.out.println("spirebot: the panel could not be started: " + e);
        }
        try {
            BridgeProcess.start();
        } catch (Throwable e) {
            System.out.println("spirebot: the bridge could not be set up: " + e);
        }
    }
}
