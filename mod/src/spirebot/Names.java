package spirebot;

import basemod.BaseMod;
import basemod.interfaces.PostInitializeSubscriber;
import com.megacrit.cardcrawl.cards.AbstractCard;
import com.megacrit.cardcrawl.core.CardCrawlGame;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.helpers.CardLibrary;
import com.megacrit.cardcrawl.helpers.PotionHelper;
import com.megacrit.cardcrawl.helpers.RelicLibrary;
import com.megacrit.cardcrawl.localization.MonsterStrings;
import com.megacrit.cardcrawl.monsters.AbstractMonster;
import com.megacrit.cardcrawl.potions.AbstractPotion;
import com.megacrit.cardcrawl.relics.AbstractRelic;
import communicationmod.ChoiceScreenUtils;
import communicationmod.CommunicationMod;
import communicationmod.OnStateChangeSubscriber;

import java.nio.charset.Charset;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The game's own names for the panel, found by id. The bridge writes each name in a label as a marker,
 * "\u0001" + kind|keys...|fallback + "\u0002" (bridge/spire_bot/panel.py), because the names it gets from
 * CommunicationMod can be wrong: CommunicationMod writes the state in the system's charset, and on a Windows set to
 * English a game in Chinese reached the bridge with every name as question marks, the ids intact. Here the name comes
 * from the game itself, in its current language:
 *   c|card id|upgrades   the card library's name, "+" when upgraded (Searing Blow "+n")
 *   m|index|monster id|n the monster at that place in the fight when it is that monster, else its name by id; " n"
 *                        when the bridge numbered it among others of the same kind
 *   p|potion id, r|relic id
 *   x|place              an option without an id (an event's button): the game's choice list at that place, from the
 *                        last few lists CommunicationMod published, the one whose entry there reads as the fallback
 *                        once written in the system's charset (the way the bridge got it); none: the fallback
 * Anything not found shows the fallback, the name as the bridge got it.
 */
public class Names implements PostInitializeSubscriber, OnStateChangeSubscriber {
    private static final Pattern MARK = Pattern.compile("\u0001([^\u0002]*)\u0002");
    private static final Deque<List<String>> choiceLists = new ArrayDeque<>();  // newest first

    public static void start() {
        BaseMod.subscribe(new Names());
    }

    @Override
    public void receivePostInitialize() {
        // (after every mod's initializer: CommunicationMod makes its subscriber list in its constructor)
        CommunicationMod.subscribe(this);
    }

    @Override
    public void receiveOnStateChange() {
        try {
            ArrayList<String> c = ChoiceScreenUtils.getCurrentChoiceList();
            synchronized (choiceLists) {
                choiceLists.addFirst(new ArrayList<>(c));
                while (choiceLists.size() > 8) choiceLists.removeLast();
            }
        } catch (Throwable e) {
            // not a screen with choices
        }
    }

    /** the text with every marker replaced by the game's name */
    public static String resolve(String text) {
        if (text == null || text.indexOf('\u0001') < 0) return text;
        Matcher m = MARK.matcher(text);
        StringBuffer out = new StringBuffer();
        while (m.find()) {
            String[] f = m.group(1).split("\\|", -1);
            String fallback = f[f.length - 1];
            String name;
            try {
                name = name(f);
            } catch (Throwable e) {
                name = null;
            }
            m.appendReplacement(out, Matcher.quoteReplacement(name == null || name.isEmpty() ? fallback : name));
        }
        m.appendTail(out);
        return out.toString();
    }

    private static String name(String[] f) {
        switch (f[0]) {
            case "c": {
                AbstractCard c = CardLibrary.getCard(f[1]);
                if (c == null) return null;
                int up = Integer.parseInt(f[2]);
                if (up <= 0) return c.name;
                return "Searing Blow".equals(f[1]) ? c.name + "+" + up : c.name + "+";
            }
            case "m": {
                int k = Integer.parseInt(f[1]);
                int n = Integer.parseInt(f[3]);
                String name = null;
                if (AbstractDungeon.getMonsters() != null && k >= 0 && k < AbstractDungeon.getMonsters().monsters.size()) {
                    AbstractMonster mo = AbstractDungeon.getMonsters().monsters.get(k);
                    if (f[2].equals(mo.id)) name = mo.name;
                }
                if (name == null) {
                    MonsterStrings s = CardCrawlGame.languagePack.getMonsterStrings(f[2]);
                    name = s == null ? null : s.NAME;
                }
                return name == null ? null : (n > 0 ? name + " " + n : name);
            }
            case "p": {
                AbstractPotion p = PotionHelper.getPotion(f[1]);
                return p == null ? null : p.name;
            }
            case "r": {
                AbstractRelic r = RelicLibrary.getRelic(f[1]);
                return r == null || !f[1].equals(r.relicId) ? null : r.name;  // (an unknown id gives the Circlet)
            }
            case "x": {
                int k = Integer.parseInt(f[1]);
                String want = f[f.length - 1];
                synchronized (choiceLists) {
                    for (List<String> c : choiceLists) {
                        if (k < c.size() && asSent(c.get(k)).equals(want)) return c.get(k);
                    }
                }
                return null;
            }
            default:
                return null;
        }
    }

    /** a string as CommunicationMod sends it (String.getBytes() in the system's charset, read back the same way) */
    private static String asSent(String s) {
        Charset cs = Charset.defaultCharset();
        return new String(s.getBytes(cs), cs);
    }
}
