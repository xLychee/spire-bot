package spirebot;

import basemod.BaseMod;
import basemod.interfaces.PostRenderSubscriber;
import basemod.interfaces.PostUpdateSubscriber;
import com.badlogic.gdx.Gdx;
import com.badlogic.gdx.Input;
import com.badlogic.gdx.graphics.Color;
import com.badlogic.gdx.graphics.Texture;
import com.badlogic.gdx.graphics.g2d.GlyphLayout;
import com.badlogic.gdx.graphics.g2d.SpriteBatch;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.megacrit.cardcrawl.core.Settings;
import com.megacrit.cardcrawl.helpers.FontHelper;
import com.megacrit.cardcrawl.helpers.ImageMaster;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;

/**
 * The bot's panel: a small box in the top right corner that shows what the bridge wrote into spirebot_status.json
 * (the bot's state, its three best options or the reason it stopped, the key hints). All text comes from the bridge,
 * already in the game's language, except the names of cards, monsters, potions, relics and options: those the bridge
 * marks by id and Names puts in from the game itself (the names the bridge gets can be question marks).
 *
 * F9 pauses and resumes the bot (written to spirebot_control.json, which the bridge reads before each command).
 * F8 hides and shows the panel. F7 switches between the thinking view and the plain three lines.
 *
 * The thinking view (when the bridge sends "viz"): the network's five best options for the step just taken as bars
 * as long as their shares, the one the bot takes highlighted; how sure the network is; the model's size and the
 * time the step's inference took; and a small curve of the network's own value estimate at its last 20 decisions
 * (cards played in a fight count one each; the newest at the right). Its height is relative: the lowest and highest
 * of those values span the box; all equal, a flat line in the middle. A mark shows where an act ends.
 *
 * The panel takes no input away from the game: it has no hitbox, it is only drawn after the game's own rendering,
 * and the two keys are read without consuming them.
 */
public class Panel implements PostUpdateSubscriber, PostRenderSubscriber {
    static final File STATUS = new File("spirebot_status.json");
    static final File CONTROL = new File("spirebot_control.json");

    private static Panel instance = null;
    private boolean hidden = false;
    private boolean paused = false;
    private int presses = 0;
    private float sinceRead = 1f;
    private long seen = -1L;
    private String title = null;
    private String hint = "";
    private final List<String> lines = new ArrayList<>();
    private boolean plain = false;  // F7: the plain three lines instead of the thinking view
    private boolean viz = false;
    private final List<String> rowText = new ArrayList<>();
    private final List<Float> rowShare = new ArrayList<>();
    private int chosen = -1;
    private String sure = "", model = "", curveLabel = "";
    private float[] curveV = new float[0];
    private int[] curveF = new int[0];

    public static void start() {
        // a pause left over from an earlier session must not hold the bot now
        CONTROL.delete();
        STATUS.delete();
        CONTROL.deleteOnExit();
        STATUS.deleteOnExit();
        instance = new Panel();
        BaseMod.subscribe(instance);
    }

    /** What F9 does: pause the bot, or let it go on. */
    public static void togglePause() {
        Panel p = instance;
        if (p == null) return;
        p.paused = !p.paused;
        p.presses++;
        try {
            Files.write(CONTROL.toPath(),
                    ("{\"paused\": " + p.paused + ", \"n\": " + p.presses + "}").getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            p.paused = !p.paused; // not written: the bot goes on as before
        }
    }

    /** What F8 does: hide the panel, or show it again. */
    public static void toggleHidden() {
        if (instance != null) instance.hidden = !instance.hidden;
    }

    /** What F7 does: the thinking view, or the plain three lines. */
    public static void togglePlain() {
        if (instance != null) instance.plain = !instance.plain;
    }

    @Override
    public void receivePostUpdate() {
        if (Gdx.input.isKeyJustPressed(Input.Keys.F8)) toggleHidden();
        if (Gdx.input.isKeyJustPressed(Input.Keys.F9)) togglePause();
        if (Gdx.input.isKeyJustPressed(Input.Keys.F7)) togglePlain();
        sinceRead += Gdx.graphics.getDeltaTime();
        if (sinceRead < 0.2f) return;
        sinceRead = 0f;
        if (!STATUS.exists()) return;
        try {
            // read in one go and close at once: the bridge replaces the file at every step, and on Windows it
            // cannot while this side holds it open
            String text = new String(Files.readAllBytes(STATUS.toPath()), StandardCharsets.UTF_8);
            JsonObject o = new JsonParser().parse(text).getAsJsonObject();
            // the bridge numbers what it writes. (The file's modification time would not do: it can have a
            // resolution of a whole second, and two writes in one second would look like one.)
            long n = o.has("n") ? o.get("n").getAsLong() : -1L;
            if (n == seen) return;
            title = Names.resolve(o.get("title").getAsString());
            hint = o.has("hint") ? Names.resolve(o.get("hint").getAsString()) : "";
            lines.clear();
            JsonArray a = o.getAsJsonArray("lines");
            for (int i = 0; a != null && i < a.size() && i < 3; i++) lines.add(Names.resolve(a.get(i).getAsString()));
            readViz(o.has("viz") && o.get("viz").isJsonObject() ? o.getAsJsonObject("viz") : null);
            seen = n;
        } catch (Exception e) {
            // half-written or locked: try again at the next look
        }
    }

    private void readViz(JsonObject v) {
        viz = false;
        rowText.clear();
        rowShare.clear();
        if (v == null) return;
        JsonArray rows = v.getAsJsonArray("rows");
        for (int i = 0; rows != null && i < rows.size() && i < 6; i++) {
            JsonArray r = rows.get(i).getAsJsonArray();
            rowText.add(Names.resolve(r.get(0).getAsString()));
            rowShare.add(r.get(1).getAsFloat());
        }
        chosen = v.has("chosen") ? v.get("chosen").getAsInt() : -1;
        sure = v.has("sure") ? v.get("sure").getAsString() : "";
        model = v.has("model") ? v.get("model").getAsString() : "";
        curveLabel = v.has("curve_label") ? v.get("curve_label").getAsString() : "";
        JsonArray c = v.has("curve") && v.get("curve").isJsonArray() ? v.getAsJsonArray("curve") : null;
        int m = c == null ? 0 : c.size();
        curveV = new float[m];
        curveF = new int[m];
        for (int i = 0; i < m; i++) {
            JsonArray p = c.get(i).getAsJsonArray();
            curveF[i] = p.get(0).getAsInt();
            curveV[i] = p.get(1).getAsFloat();
        }
        viz = !rowText.isEmpty();
    }

    private static final Color BAR_CHOSEN = new Color(0.94f, 0.72f, 0.25f, 0.85f);
    private static final Color CHOSEN_EDGE = new Color(1f, 0.85f, 0.35f, 1f);
    private static final Color BAR_OTHER = new Color(0.55f, 0.6f, 0.7f, 0.45f);
    private static final Color BAR_BACK = new Color(1f, 1f, 1f, 0.07f);
    private static final Color CURVE = new Color(0.45f, 0.85f, 1f, 1f);
    private static final Color GRID = new Color(1f, 1f, 1f, 0.18f);
    private static final Color SURE_C = new Color(0.5f, 0.9f, 0.5f, 1f);
    private static final Color FAIR_C = new Color(0.95f, 0.85f, 0.4f, 1f);
    private static final Color TORN_C = new Color(1f, 0.55f, 0.35f, 1f);

    private static void rect(SpriteBatch sb, Color c, float x, float y, float w, float h) {
        sb.setColor(c);
        sb.draw(ImageMaster.WHITE_SQUARE_IMG, x, y, w, h);
    }

    /** a straight line of width t from (x1, y1) to (x2, y2): the white square, stretched and turned */
    private static void line(SpriteBatch sb, Color c, float x1, float y1, float x2, float y2, float t) {
        Texture tex = ImageMaster.WHITE_SQUARE_IMG;
        float dx = x2 - x1, dy = y2 - y1, len = (float) Math.sqrt(dx * dx + dy * dy);
        if (len < 0.01f) return;
        sb.setColor(c);
        sb.draw(tex, x1, y1 - t / 2f, 0f, t / 2f, len, t, 1f, 1f, (float) Math.toDegrees(Math.atan2(dy, dx)),
                0, 0, tex.getWidth(), tex.getHeight(), false, false);
    }

    /** the act of a floor (each act's boss chest counts with its act) */
    private static int act(int floor) {
        return floor <= 17 ? 1 : floor <= 34 ? 2 : floor <= 51 ? 3 : 4;
    }

    /** text cut to fit width, with an ellipsis */
    private String fit(String text, float width) {
        layout.setText(FontHelper.tipBodyFont, text);
        if (layout.width <= width) return text;
        int end = text.length();
        while (end > 1) {
            end--;
            layout.setText(FontHelper.tipBodyFont, text.substring(0, end) + "…");
            if (layout.width <= width) break;
        }
        return text.substring(0, end) + "…";
    }

    private float textWidth(String text) {
        layout.setText(FontHelper.tipBodyFont, text);
        return layout.width;
    }

    @Override
    public void receivePostRender(SpriteBatch sb) {
        if (hidden) return;
        if (viz && !plain) {
            renderViz(sb);
            return;
        }
        float s = Settings.scale;
        float w = 420f * s, pad = 12f * s, lineH = 30f * s;
        float textW = w - 2f * pad;
        String head = title != null ? title
                : (Settings.language == Settings.GameLanguage.ZHS || Settings.language == Settings.GameLanguage.ZHT
                ? "等待机器人…" : "Waiting for the bot...");
        // each line wrapped to the box's width by measuring it (the game's own wrapped-text drawing drops the
        // spaces of Chinese text)
        List<String> rows = new ArrayList<>();
        for (String line : lines) wrap(line, textW, rows);
        float h = pad + lineH * (rows.size() + 2) + pad;
        float x = Settings.WIDTH - w - 16f * s;
        float top = Settings.HEIGHT - 170f * s;
        sb.setColor(0f, 0f, 0f, 0.7f);
        sb.draw(ImageMaster.WHITE_SQUARE_IMG, x, top - h, w, h);
        sb.setColor(Color.WHITE);
        float y = top - pad;
        FontHelper.renderFontLeftTopAligned(sb, FontHelper.tipBodyFont, "Spire Bot: " + head, x + pad, y, Settings.GOLD_COLOR);
        y -= lineH;
        for (String row : rows) {
            FontHelper.renderFontLeftTopAligned(sb, FontHelper.tipBodyFont, row, x + pad, y, Settings.CREAM_COLOR);
            y -= lineH;
        }
        FontHelper.renderFontLeftTopAligned(sb, FontHelper.tipBodyFont, hint, x + pad, y, Color.LIGHT_GRAY);
    }

    private void renderViz(SpriteBatch sb) {
        float s = Settings.scale;
        float w = 460f * s, pad = 12f * s, lineH = 30f * s, rowH = 32f * s, curveH = 84f * s;
        float textW = w - 2f * pad;
        boolean curve = curveV.length >= 1;
        float h = pad + lineH + rowH * rowText.size() + lineH * 2 + (curve ? lineH + curveH + 6f * s : 0f) + lineH + pad;
        float x = Settings.WIDTH - w - 16f * s;
        float top = Settings.HEIGHT - 170f * s;
        rect(sb, new Color(0f, 0f, 0f, 0.72f), x, top - h, w, h);
        float y = top - pad;
        sb.setColor(Color.WHITE);
        FontHelper.renderFontLeftTopAligned(sb, FontHelper.tipBodyFont, "Spire Bot: " + (title != null ? title : ""),
                x + pad, y, Settings.GOLD_COLOR);
        y -= lineH;
        float pctW = textWidth("100%") + 8f * s;
        for (int i = 0; i < rowText.size(); i++) {
            float q = Math.max(0f, Math.min(1f, rowShare.get(i)));
            float by = y - rowH + 3f * s, bh = rowH - 6f * s;
            rect(sb, BAR_BACK, x + pad, by, textW, bh);
            if (i == chosen) rect(sb, CHOSEN_EDGE, x + pad - 3f * s, by - 2f * s, 4f * s, bh + 4f * s);
            if (q > 0f) rect(sb, i == chosen ? BAR_CHOSEN : BAR_OTHER, x + pad, by, Math.max(2f * s, textW * q), bh);
            Color tc = i == chosen ? Color.WHITE : Settings.CREAM_COLOR;
            String lab = rowText.get(i);
            sb.setColor(Color.WHITE);
            FontHelper.renderFontLeftTopAligned(sb, FontHelper.tipBodyFont, fit(lab, textW - pctW - 8f * s),
                    x + pad + 6f * s, y - 2f * s, tc);
            String pct = String.format("%.0f%%", 100f * q);
            FontHelper.renderFontLeftTopAligned(sb, FontHelper.tipBodyFont, pct, x + w - pad - 6f * s - textWidth(pct),
                    y - 2f * s, tc);
            y -= rowH;
        }
        Color sc = rowShare.isEmpty() ? FAIR_C : rowShare.get(0) >= 0.9f ? SURE_C : rowShare.get(0) >= 0.6f ? FAIR_C : TORN_C;
        FontHelper.renderFontLeftTopAligned(sb, FontHelper.tipBodyFont, sure, x + pad, y, sc);
        y -= lineH;
        FontHelper.renderFontLeftTopAligned(sb, FontHelper.tipBodyFont, fit(model, textW), x + pad, y, Color.GRAY);
        y -= lineH;
        if (curve) {
            FontHelper.renderFontLeftTopAligned(sb, FontHelper.tipBodyFont, curveLabel, x + pad, y, Settings.CREAM_COLOR);
            y -= lineH;
            float cx = x + pad, cy = y - curveH, cw = textW;
            rect(sb, BAR_BACK, cx, cy, cw, curveH);
            float lo = Float.MAX_VALUE, hi = -Float.MAX_VALUE;
            for (float v : curveV) {
                lo = Math.min(lo, v);
                hi = Math.max(hi, v);
            }
            boolean flat = hi - lo < 1e-6f;
            float margin = (hi - lo) * 0.1f;
            lo -= margin;
            hi += margin;
            int n = curveV.length;
            float px = 0f, py = 0f;
            for (int i = 0; i < n; i++) {
                float vx = n == 1 ? cx + cw : cx + cw * i / (n - 1f);  // the newest at the right
                float vy = flat ? cy + curveH / 2f : cy + curveH * (curveV[i] - lo) / (hi - lo);
                if (i > 0 && act(curveF[i]) != act(curveF[i - 1])) {
                    line(sb, GRID, vx, cy, vx, cy + curveH, 1.5f * s);  // an act ended here
                }
                if (i > 0) line(sb, CURVE, px, py, vx, vy, 2.5f * s);
                px = vx;
                py = vy;
            }
            rect(sb, Color.WHITE, px - 3f * s, py - 3f * s, 6f * s, 6f * s);
            y -= curveH + 6f * s;
        }
        sb.setColor(Color.WHITE);
        FontHelper.renderFontLeftTopAligned(sb, FontHelper.tipBodyFont, hint, x + pad, y, Color.LIGHT_GRAY);
        sb.setColor(Color.WHITE);
    }

    private final GlyphLayout layout = new GlyphLayout();

    /** Break text into rows no wider than width (at any character: Chinese has no spaces to break at). */
    private void wrap(String text, float width, List<String> out) {
        int start = 0, added = 0;
        while (start < text.length() && added < 6) {
            int end = text.length();
            layout.setText(FontHelper.tipBodyFont, text.substring(start, end));
            while (layout.width > width && end > start + 1) {
                end -= Math.max(1, (int) ((end - start) * (1f - width / layout.width) * 0.9f));
                layout.setText(FontHelper.tipBodyFont, text.substring(start, end));
            }
            if (end < text.length()) {  // prefer a space near the end of the row, if there is one
                int sp = text.lastIndexOf(' ', end);
                if (sp > start + (end - start) / 2) end = sp + 1;
            }
            out.add(text.substring(start, end).trim());
            start = end;
            added++;
        }
    }
}
