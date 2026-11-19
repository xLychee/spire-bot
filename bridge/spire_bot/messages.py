"""Every sentence the bot shows a player, with a code, in English and Chinese.

logs/bot.log is always English, each line led by the code. The in-game panel shows the same message in the game's
language (Chinese for a game in Simplified or Traditional Chinese, English otherwise).
"""
from __future__ import annotations

import json

from . import config as CFG

TABLE = {
    # the panel's own words
    "S_PLAYING": ("Playing", "正在打"),
    "S_PAUSED": ("Paused", "已暂停"),
    "S_STOPPED": ("Stopped", "已停下"),
    "S_FINISHED": ("Run over", "这一局结束"),
    "S_REFUSED": ("Not playing", "没有开打"),
    "S_STARTING": ("Starting", "准备开局"),
    "FLOOR": ("Floor {n}", "第 {n} 层"),
    "HINT": ("F9 pause   F8 hide", "F9 暂停   F8 隐藏"),
    "HINT_PAUSED": ("F9 resume   F8 hide", "F9 接着打   F8 隐藏"),
    "KEY_RULE": ("(key rule)", "（钥匙规则）"),
    # the thinking view (viz): how sure the network is, the curve's label, the model line
    "SURE": ("Very sure", "很确定"),
    "FAIRLY_SURE": ("Fairly sure", "比较确定"),
    "TORN": ("Unsure", "在纠结"),
    "ONLY_OPTION": ("Only option", "唯一选择"),
    "FEELING": ("AI's own estimate", "AI 自我感觉"),
    "MODEL_LINE": ("{params} parameters · {ms} ms", "{params}参数 · 推理 {ms} 毫秒"),
    "MODEL_PARAMS": ("{params} parameters", "{params}参数"),
    "MODEL_LINE_NOPARAMS": ("{ms} ms", "推理 {ms} 毫秒"),
    "HINT_VIZ": ("F9 pause   F8 hide   F7 view", "F9 暂停   F8 隐藏   F7 视图"),
    "HINT_PAUSED_VIZ": ("F9 resume   F8 hide   F7 view", "F9 接着打   F8 隐藏   F7 视图"),
    "END_TURN": ("End turn", "结束回合"),
    "PLAY": ("Play {card}", "打出 {card}"),
    "POTION_USE": ("Use {potion}", "使用 {potion}"),
    "POTION_DISCARD": ("Discard {potion}", "丢弃 {potion}"),
    "PROCEED": ("Proceed", "继续"),
    "SKIP": ("Skip", "跳过"),
    "LEAVE": ("Leave", "离开"),
    "CONFIRM": ("Confirm", "确认"),
    "CHOOSE": ("Choose {what}", "选择 {what}"),
    "SKIP_FOR": ("Skip, take {what}", "跳过，拿{what}"),
    "C_gold_THE": ("the gold", "金币"), "C_relic_THE": ("the relic", "遗物"), "C_potion_THE": ("the potion", "药水"),
    "MAP_NODE": ("Path at column {x}", "走第 {x} 列的路"),
    # choices CommunicationMod names with fixed English words, whatever the game's language
    "C_shop": ("Enter the shop", "进商店"), "C_rest": ("Rest", "休息"), "C_smith": ("Smith", "锻造"),
    "C_recall": ("Recall (ruby key)", "回忆（红钥匙）"), "C_dig": ("Dig", "挖掘"), "C_lift": ("Lift", "举重"),
    "C_toke": ("Toke", "冥想"), "C_open": ("Open the chest", "打开宝箱"), "C_purge": ("Remove a card", "移除一张牌"),
    "C_boss": ("Boss", "Boss"), "C_gold": ("Gold", "金币"), "C_stolen_gold": ("Stolen gold", "被偷的金币"),
    "C_relic": ("Relic", "遗物"), "C_potion": ("Potion", "药水"), "C_card": ("Card reward", "卡牌奖励"),
    "C_emerald_key": ("Emerald key", "绿钥匙"), "C_sapphire_key": ("Sapphire key", "蓝钥匙"),
    "WON": ("The Heart is beaten.", "打赢了心脏。"),
    "LOST": ("The run ended on floor {n}.", "这一局在第 {n} 层结束。"),
    # why the bot stopped in a run
    "R00": ("the run is over", "这一局结束了"),
    "R01": ("5 errors in a row", "游戏连续 5 次报错"),
    "R02": ("not in a game, and cannot start", "不在游戏里，也不能开局"),
    "R03": ("the same state repeated 30 times", "同一个局面重复了 30 次"),
    "R04": ("sent {n} commands", "已经发了 {n} 条命令"),
    "R05": ("none of the commands tried on the same state changed it: {options}",
            "在同一个局面上试过的命令都没让局面变化：{options}"),
    "R06": ("unrecognized state: {screen}, available {av}", "不认识的局面：{screen}，可用命令 {av}"),
    "R07": ("the simulator did not keep up with the game", "模拟器没跟上游戏"),
    "R08": ("cannot translate: {note}", "这一步没法换成游戏命令：{note}"),
    "R09": ("asked for the state 12 times (1 minute); the game never became ready for a command",
            "问了 12 次局面（1 分钟），游戏一直没准备好接命令"),
    "R10": ("error: {error}", "出错了：{error}"),
    "R11": ("the game was changed while the bot was paused and the bot cannot follow; please finish this run yourself",
            "暂停时游戏被改动过，机器人跟不上，这一局请自己打完"),
    # why the bot does not start
    "P01": ("The game's preferences folder was not found at {path}. The bot must be started by the game "
            "(CommunicationMod), from the game's own folder.",
            "找不到游戏的 preferences 目录（{path}）。机器人要由游戏（CommunicationMod）从游戏自己的目录启动。"),
    "P02": ("The game's profile files could not be read (preferences/STSPlayer, STSDataVagabond, STSUnlockProgress). "
            "Play the game once without the bot, then try again.",
            "读不了游戏的存档资料（preferences 里的 STSPlayer、STSDataVagabond、STSUnlockProgress）。先不带机器人玩一次游戏，再试。"),
    "P03": ("Act 4 is not unlocked in this profile (it needs a win with Ironclad, Silent and Defect). The bot plays "
            "for the Heart and needs Act 4.",
            "这个存档还没解锁第四幕（要用铁甲战士、静默猎手、故障机器人各通关一次）。机器人的目标是心脏，需要第四幕。"),
    "P04": ("Ascension {want} is not unlocked for Ironclad in this profile (highest: {have}). Set ascension = {have} "
            "or lower in spire-bot.toml.",
            "这个存档里铁甲战士还没解锁进阶 {want}（最高 {have}）。把 spire-bot.toml 里的 ascension 改成 {have} 或更低。"),
    "P05": ("Ironclad's cards are not all unlocked in this profile (unlock level {level} of 5). The bot needs all "
            "Ironclad cards unlocked.",
            "这个存档里铁甲战士的牌没有全部解锁（解锁等级 {level}/5）。机器人需要铁甲战士的牌全部解锁。"),
    "P06": ("The model file could not be loaded ({path}): {error}", "模型文件读不了（{path}）：{error}"),
    "P07": ("A library could not be loaded ({error}). On Windows this usually means the Microsoft Visual C++ "
            "Redistributable (x64) is missing: install it from https://aka.ms/vs/17/release/vc_redist.x64.exe and "
            "start the game again.",
            "有一个库加载不了（{error}）。在 Windows 上通常是缺微软的 Visual C++ 运行库（x64）：从 "
            "https://aka.ms/vs/17/release/vc_redist.x64.exe 安装，再重新启动游戏。"),
    "P08": ("A run is already open in the game. Finish or abandon it, go back to the main menu and restart the "
            "game: the bot only plays runs it starts itself.",
            "游戏里已经开着一局。把它打完或放弃，回到主菜单后重启游戏：机器人只打它自己开的局。"),
    "P09": ("There is an unfinished Ironclad run (the main menu shows Continue). The bot would overwrite it by "
            "starting a new run: finish or abandon it first, then restart the game.",
            "有一局没打完的铁甲战士存档（主菜单上有“继续”）。机器人开新局会把它冲掉：先打完或放弃，再重启游戏。"),
    "P10": ("The bot's own mod (Spire Bot, spirebot) is not loaded. Tick it in the ModTheSpire launcher next to "
            "BaseMod and CommunicationMod.",
            "机器人自己的 mod（Spire Bot，spirebot）没有加载。在 ModTheSpire 启动器里把它和 BaseMod、CommunicationMod 一起勾上。"),
}


class Msg(str):
    """A message as a string ("[CODE] English text", what the log gets) that also carries its code and its Chinese."""

    def __new__(cls, code: str, **kw):
        en, zh = TABLE[code]
        s = super().__new__(cls, f"[{code}] {en.format(**kw)}")
        s.code, s.en, s.zh = code, en.format(**kw), zh.format(**kw)
        return s

    def shown(self, lang: str) -> str:
        return self.zh if lang == "zh" else self.en


def text(code: str, lang: str, **kw) -> str:
    return Msg(code, **kw).shown(lang)


def shown(m, lang: str) -> str:
    """m in the panel's language: a Msg's own text, any other string as it is"""
    return m.shown(lang) if isinstance(m, Msg) else str(m)


def game_language() -> str:
    """"zh" when the game is set to Simplified or Traditional Chinese, else "en" """
    try:
        d = json.loads((CFG.PREFS_DIR / "STSGameplaySettings").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "en"
    return "zh" if str(d.get("LANGUAGE", "")).upper() in ("ZHS", "ZHT") else "en"
