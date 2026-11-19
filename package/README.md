# Spire Bot

A neural network that plays Slay the Spire (Ironclad, up to Ascension 20, going for the Heart) in your own copy of
the game. You start the game with mods; the bot starts a run from the main menu and plays it to the end by itself.

No game files are included. You need Slay the Spire on Steam.

## What you need

- Slay the Spire (Steam), Windows 10/11 64-bit, or a Mac with Apple silicon (macOS 13 or later).
- A profile with **Act 4 unlocked**, **all Ironclad cards unlocked**, and the Ascension level you want the bot to
  play unlocked for Ironclad (20 by default). The bot checks this and tells you in `logs/bot.log` if something is
  missing; it does not play then.
- No unfinished Ironclad run. Starting a run would overwrite it, so the bot refuses while the main menu shows
  "Continue" for Ironclad.
- No other mods that change cards, relics, monsters or events. The bot only knows the base game.

## Install from the Steam Workshop (Windows and Mac)

1. In the Steam Workshop for Slay the Spire, subscribe to **ModTheSpire**, **BaseMod**, **Communication Mod** and
   **Spire Bot**. Spire Bot brings its own Python, simulator and model; there is nothing to unzip or run.
2. Start Slay the Spire from Steam and choose **Play With Mods**. In the ModTheSpire window tick **BaseMod**,
   **Communication Mod** and **Spire Bot**, then press **Play**.
3. At the main menu the bot starts a run within a few seconds. Do not click or press keys while it plays.

Leave Communication Mod's own `command` setting empty: Spire Bot starts its bridge itself and does not start while
Communication Mod is set to start another program (two programs would both send commands to the game; the panel says
so). Its logs are in ModTheSpire's settings folder, `spirebot/logs` (Windows `%LOCALAPPDATA%\ModTheSpire\spirebot\logs`,
Mac `~/Library/Preferences/ModTheSpire/spirebot/logs`). To change the settings, copy `spire-bot.toml` from the item's
`spire-bot` folder to `spirebot/spire-bot.toml` there and edit that copy: the item's own copy is replaced when it
updates.

If you installed Spire Bot from the zip before, run its uninstall first (`uninstall.bat` or `uninstall.sh`,
see [Uninstall](#uninstall)), then subscribe: only one copy of Spire Bot should be in the game.

## Install from the zip (Windows)

If you subscribed to Spire Bot in the Steam Workshop, unsubscribe first: only one copy of Spire Bot should be in the
game. (The other three mods stay subscribed.)

1. In the Steam Workshop for Slay the Spire, subscribe to **ModTheSpire**, **BaseMod** and **Communication Mod**.
2. Unzip `spire-bot` into a folder of your choice, for example `C:\spire-bot`.
3. Download the model file `model.onnx` (link on the release page) and put it into `spire-bot\models\`.
4. Close Slay the Spire if it is running, then double-click `install.bat`. Windows may show "Windows protected your
   PC" for a downloaded file: click **More info**, then **Run anyway**. It finds the game, copies `spirebot.jar` into
   the game's `mods` folder and tells the mod where this folder is. If it cannot find the game, run
   `install.bat --game "D:\path\to\SlayTheSpire"`.
5. Start Slay the Spire from Steam and choose **Play With Mods**. In the ModTheSpire window tick **BaseMod**,
   **Communication Mod** and **Spire Bot**, then press **Play**.
6. At the main menu the bot starts a run within a few seconds. Do not click or press keys while it plays.

## Install from the zip (Mac, Apple silicon)

As on Windows, unsubscribe from Spire Bot in the Workshop first if you subscribed. Steps 1, 3, 5 and 6 as above. For 2 and 4: unzip into a folder of your choice (for example `~/spire-bot`), then in
Terminal:

    xattr -dr com.apple.quarantine ~/spire-bot      # macOS blocks unsigned downloads otherwise
    ~/spire-bot/install.sh

Close the game first. If the script cannot find the game, run `~/spire-bot/install.sh --game "/path/to/the/folder/with/desktop-1.0.jar"`.

## The panel in the game

A small box in the top right corner shows what the bot is doing: playing, paused or stopped. When the bot stops or
does not start, the reason is shown there, the same message as in `logs/bot.log`.

While it plays, the box shows the bot's "thinking":
- its five best options for the step, each with a bar as long as the network's probability for it (its scores over
  the options it chose from, as percentages). The one highlighted in gold is the step it actually takes.
- below, how sure it is: "Very sure" when the largest probability is 90% or more, "Fairly sure" from 60% to 90%,
  "Unsure" below 60%.
- a small line with the model's number of parameters and how many milliseconds the step took.
- the options are named as the game names them; two monsters with the same name are numbered left to right
  ("Louse 1", "Louse 2"), and options that would still read alike get a number in brackets.
- at the bottom, a small curve, "AI's own estimate": the network's value head, its own estimate of how the run is
  going, at its last 20 decisions (each card played in a fight counts as one), the newest at the right. Its height
  spans the lowest and highest of those 20 values, so the ups and downs show; a vertical mark is where an act ends.
  It shows only higher and lower, not a chance of winning; early in act 1 the network cannot yet tell good runs from
  bad ones. (With a model file that has no value head the curve is not drawn.)

- **F9** pauses the bot and lets it go on. The command already sent is finished; nothing more is sent until you press
  F9 again. Do not play while it is paused: if the game has changed in a way the bot cannot follow, it stops and
  asks you to finish the run yourself.
- **F8** hides and shows the panel.
- **F7** switches between the thinking view and a plain view of the three best options.

The panel follows the game's language (Chinese or English). To switch it off entirely, add `panel = "0"` to
`spire-bot.toml`; for the plain view only, add `viz = "0"`.

## Settings

`spire-bot.toml` in the package folder (from the Workshop: your copy in ModTheSpire's settings folder, see above): the
model file, the Ascension level, and the seed (empty = random each time).
To use another model, put its `.onnx` file into `models/` and set `model = "its-name.onnx"`.

## While it plays, and afterwards

- `logs/bot.log` says what the bot is doing, and why it is not playing if it is not.
- Each run leaves a folder in `logs/` with every game state and command. These stay on your computer; nothing is
  sent anywhere. They contain game states only.
- Runs started by the bot use a set seed, like any seeded run you start yourself.
- The bot's runs are ordinary runs of your profile: they appear in your run history and count in your statistics.
- When the run ends the bot stops. Restart the game to have it play another one.
- To play yourself again: start the game without mods, or untick Communication Mod and Spire Bot.

## Uninstall

From the Workshop: unsubscribe. From the zip: run `uninstall.bat` (Windows) or `uninstall.sh` (Mac), then delete the
folder. This removes `spirebot.jar` from the game and the mod's note of where the folder was; if an older version of
this bot had set Communication Mod's command, its settings are put back as they were before.

## If something goes wrong

- Nothing happens at the main menu: look at the panel in the top right corner and read `logs/bot.log`. The panel
  says "Spire Bot is not started" when Communication Mod is set to start another program (clear its `command`
  setting) or the bot's own folder was not found (subscribe again, or run install again).
- The bot stops in the middle of a run: the game showed something the bot's simulator could not follow. The reason is
  in `logs/bot.log`; the run's folder in `logs/` is what to send with a bug report.

## Known limits of this version

- **The Windows package has not been run on a Windows computer yet.** It was built and checked on a Mac: the same
  bridge, model and simulator code played real games there, and the Windows simulator module was checked statically
  (it needs only the package's own Python and system libraries). What has not been seen working on Windows: the
  simulator module loading, the install script finding the game, and starting through Steam's "Play With Mods"
  launcher. If the bot does not start, `logs/bot.log` (or its absence) and the ModTheSpire log window say where it
  stopped; please report it.
- Windows: if `logs/bot.log` says a library could not be loaded, the Microsoft Visual C++ runtime is missing (see
  Technical notes).
- Tested with the game in Simplified Chinese. Other languages should work (the bot reads ids, not names) but have
  not been played.
- F9 has been pressed in a real game on a Mac (it paused the bot and let it go on); F8 and F7 have only been tested
  by calling their functions directly, not on a keyboard. On a Mac laptop the keys are Fn+F9 and Fn+F8 unless the
  function keys are set to act as standard function keys.
- After some card choices in a fight the bot has to work out the order of the cards the game will deal. Usually this
  takes no time; in the slowest case seen the game waits about 10 seconds once (longer on a slower computer) before
  the bot goes on.
- At the start of a fight the bot waits a moment until the game shows what the monsters intend to do, at most about
  5 seconds; with Runic Dome, which hides it, the bot waits half a second.
- One run per game start. The bot occasionally stops in the middle of a run when the game does something its
  simulator cannot follow; restart the game for a new run.

## Technical notes

- The network scores every legal option and the bot takes the best one. Options whose scores are within 0.0001 of the
  best count as tied and the first of them is taken: equivalent options get scores that differ only in the last
  bits, which depend on the platform, and this rule makes the bot choose the same on every computer.
- Windows: the neural-network library needs the Microsoft Visual C++ runtime, which most gaming PCs already have. If
  `logs/bot.log` says a library could not be loaded, install it from
  https://aka.ms/vs/17/release/vc_redist.x64.exe and start the game again.

## Licenses

See `LICENSES/README.md`. The bridge and the Java mod are open source (MIT); the simulator (`spire_sim`) is distributed
compiled only (free to use and to pass on unchanged; no modification, no reverse engineering); the model weights are
CC BY 4.0. Slay the Spire is a game by Mega Crit; this project is not affiliated with Mega Crit.
