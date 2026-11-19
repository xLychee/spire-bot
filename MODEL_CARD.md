# Spire Bot model

A neural network that plays Slay the Spire as the Ironclad at Ascension 20, going for the Heart. It is the model file
the Spire Bot package loads (`models/model.onnx`).

- **File format**: ONNX (about 22 million parameters, 92 MB), with the metadata entry `format = spire-bot-model-1`.
  The inputs are the features the package's simulator (`spire_sim`) produces for a decision. The outputs are `logits`,
  one score for each legal option, and two estimates of how the run will go (`value`, `bvalue`) that the bot's panel
  and its search in elite and boss fights use. The batch size and the number of options are variable.
- **What it decides**: every choice in a run: cards to play and their targets, potions, card rewards, relics, events,
  shops, rest sites and the path on the map. It plays one character (Ironclad) and was made for Ascension 20.
- **How it is used**: by the Spire Bot bridge, which takes the option with the highest score (options within 0.0001
  of the best count as tied and the first of them is taken, so that every computer chooses the same). In elite and
  boss fights, when the model is unsure, the bridge also tries its best few options on copies of the fight (the cards
  still to come drawn again) and plays the one that leaves the model best off on average (`search` in
  `spire-bot.toml`; `"0"` turns it off).

## Results

Measured in the real game, Ironclad, Ascension 20, going for the Heart. Each seed was played once, from the first
start of the seed.

- **Build measured**: an earlier build of this package, on a Mac, with the SuperFastMode mod (it speeds up the game's
  animations), 220 fixed seeds from 5800001 up to 5800228, **without search** (the search was added later). The model
  weights are the ones released here; that build loaded an earlier ONNX export of the same weights (scores only, fixed
  batch size).
- **Result**: the Heart was killed in **65 of the 220 first plays (29.5%)** when every run that did not finish counts
  as a loss, and in **65 of the 215 runs that finished (30.2%)**. One standard error is about 3 percentage points.
  Every win reached floor 57 (the Heart's death).
- **Runs that did not finish (5 of 220)**: 4 stopped part-way because the bridge lost track of the game (the causes
  found have been fixed since) and 1 crashed as the game window opened.
- **The released package** (with search on by default) has been through 20-run checks on a Mac and on Windows to
  see that it plays to the end; 20 runs are too few to give a win rate, and no larger real-game measurement of it has
  been made. The numbers above are not a measurement of the search.

## Limits

- Ironclad only. Lower Ascension levels can be set in the package, but the model's play there has not been measured.
- It knows the base game only: no modded cards, relics, monsters or events.
- The search takes a few seconds at some steps in elite and boss fights.
- It sometimes ends a turn with energy and playable cards left, and sometimes plays a power that is already active
  a second time (Barricade, Corruption).
- How the model was trained is not part of this release.

## License

The model weights are licensed under the Creative Commons Attribution 4.0 International License (CC BY 4.0):
https://creativecommons.org/licenses/by/4.0/ . Attribution: "Spire Bot model by xLychee".

Slay the Spire is a game by Mega Crit. This project is not affiliated with Mega Crit.
