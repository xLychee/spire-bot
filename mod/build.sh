#!/bin/sh
# Builds build/spirebot.jar (Java 8 bytecode, the game's JRE). Needs a JDK 9+ and your own copy of the game:
#   GAME_JAR=/path/to/desktop-1.0.jar MTS_JAR=/path/to/ModTheSpire.jar BASEMOD_JAR=/path/to/BaseMod.jar \
#     COMM_JAR=/path/to/CommunicationMod.jar \
#     [JAVA_HOME=...] ./build.sh
# The game's, ModTheSpire's, BaseMod's and CommunicationMod's jars are only compiled against; nothing of them goes into
# the mod's jar.
set -eu
cd "$(dirname "$0")"
: "${GAME_JAR:?set GAME_JAR to desktop-1.0.jar of the game}" "${MTS_JAR:?set MTS_JAR to ModTheSpire.jar}" "${BASEMOD_JAR:?set BASEMOD_JAR to BaseMod.jar}" \
  "${COMM_JAR:?set COMM_JAR to CommunicationMod.jar}"
BIN=${JAVA_HOME:+$JAVA_HOME/bin/}
rm -rf build && mkdir -p build/classes
"${BIN}javac" --release 8 -nowarn -cp "$GAME_JAR:$MTS_JAR:$BASEMOD_JAR:$COMM_JAR" -d build/classes $(find src -name '*.java')
cp resources/ModTheSpire.json build/classes/
"${BIN}jar" cf build/spirebot.jar -C build/classes .
echo built build/spirebot.jar
