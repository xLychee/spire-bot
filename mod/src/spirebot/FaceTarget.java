package spirebot;

import com.evacipated.cardcrawl.modthespire.lib.SpirePatch;
import com.evacipated.cardcrawl.modthespire.lib.SpirePostfixPatch;
import com.evacipated.cardcrawl.modthespire.lib.SpirePrefixPatch;
import com.megacrit.cardcrawl.cards.AbstractCard;
import com.megacrit.cardcrawl.cards.CardQueueItem;
import com.megacrit.cardcrawl.characters.AbstractPlayer;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.monsters.AbstractMonster;
import com.megacrit.cardcrawl.potions.AbstractPotion;
import com.megacrit.cardcrawl.potions.PotionSlot;

import java.util.ArrayList;

/**
 * When the player is Surrounded (the Spire Shield and Spire Spear fight), turn the player toward the monster that a
 * card or potion from CommunicationMod's "play" and "potion use" commands is aimed at, as the game does when a person
 * plays with the mouse. CommunicationMod queues the card or uses the potion directly and never turns the player, so
 * without this the back attack stays where the fight began. Only the facing changes, and only on a command that
 * succeeded: no random number is drawn. Cards played by the game's own effects do not go through these commands and
 * do not turn the player, as in the game.
 */
public class FaceTarget {
    static void face(AbstractMonster m) {
        AbstractPlayer p = AbstractDungeon.player;
        if (m != null && p != null && p.hasPower("Surrounded")) {
            p.flipHorizontal = m.drawX < p.drawX;
        }
    }

    @SpirePatch(cls = "communicationmod.CommandExecutor", method = "executePlayCommand", requiredModId = "CommunicationMod")
    public static class Play {
        @SpirePostfixPatch
        public static void Postfix(String[] tokens) {
            // (reached only when the command succeeded) the item CommunicationMod just queued
            ArrayList<CardQueueItem> q = AbstractDungeon.actionManager.cardQueue;
            if (q.isEmpty()) return;
            CardQueueItem item = q.get(q.size() - 1);
            if (item.card != null && (item.card.target == AbstractCard.CardTarget.ENEMY
                    || item.card.target == AbstractCard.CardTarget.SELF_AND_ENEMY)) {
                face(item.monster);
            }
        }
    }

    @SpirePatch(cls = "communicationmod.CommandExecutor", method = "executePotionCommand", requiredModId = "CommunicationMod")
    public static class Potion {
        private static AbstractMonster target = null;

        @SpirePrefixPatch
        public static void Prefix(String[] tokens) {
            // the monster the command aims the potion at, as CommunicationMod reads it; applied in the postfix, which
            // is reached only when the potion was used
            target = null;
            try {
                if (tokens.length < 4 || !tokens[1].equals("use")) return;
                int i = Integer.parseInt(tokens[2]);
                if (i < 0 || i >= AbstractDungeon.player.potions.size()) return;
                AbstractPotion potion = AbstractDungeon.player.potions.get(i);
                if (potion instanceof PotionSlot || !potion.targetRequired) return;
                int k = Integer.parseInt(tokens[3]);
                ArrayList<AbstractMonster> ms = AbstractDungeon.getCurrRoom().monsters.monsters;
                if (k >= 0 && k < ms.size()) target = ms.get(k);
            } catch (RuntimeException e) {
                target = null; // CommunicationMod rejects the command itself
            }
        }

        @SpirePostfixPatch
        public static void Postfix(String[] tokens) {
            face(target);
            target = null;
        }
    }
}
