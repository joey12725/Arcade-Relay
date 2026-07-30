.pragma library
// Per-system look, and the GIF tag filter used to pick a mascot.
//
// Keys are Pegasus collection shortNames, which pegasus_gen writes from the
// /roms directory names. Anything not listed falls back to DEFAULT so a new
// system dir still renders (in the house palette) instead of blanking.

var SYSTEMS = {
    favorites: { title: "FAVORITES", code: "FAVES", sub: "best of everything :3", badge: "★",
        skin: "favorites", bg: "#eaf5ff", ink: "#4a0a2c",
        c1: "#ff2d6f", c2: "#ffd400", c3: "#0b6ed4",
        caseInk: "#c1004a", caseFace: "#ffffff", corner: "HOT!!",
        marquee: "★ UR TOP PICKS ★ ALL KILLER NO FILLER ★ PURE VIBES ONLY ★",
        gifTags: ["all"] },
    nes: { title: "NES", code: "NES", sub: "8-bit forever", badge: "8",
        skin: "nes", bg: "#f7f4f2", ink: "#3a1214",
        c1: "#e63946", c2: "#8f8f96", c3: "#2d2d33",
        caseInk: "#2b2b30", caseFace: "#e8e2d8", corner: "CLASSIC",
        marquee: "NOW YOU'RE PLAYING WITH POWER • BLOW ON THE CARTRIDGE • 8-BIT 4 LYFE •",
        gifTags: ["nes"] },
    snes: { title: "SUPER NES", code: "SNES", sub: "16-bit mode 7", badge: "16",
        skin: "snes", bg: "#f4f2fa", ink: "#2a1a4a",
        c1: "#7b2dff", c2: "#b8b8c8", c3: "#5a5a7a",
        caseInk: "#4a3a7a", caseFace: "#efeaff", corner: "SUPER!",
        marquee: "MODE 7 GOES BRRR • PURPLE GEL BUTTONS • 16-BIT SUPREMACY •",
        gifTags: ["snes"] },
    n64: { title: "NINTENDO 64", code: "N64", sub: "atomic purple era", badge: "64",
        skin: "n64", bg: "#f3f0ff", ink: "#2b0a55",
        c1: "#7b2dff", c2: "#7cff5a", c3: "#ffd400",
        caseInk: "#3a0a8a", caseFace: "#f0e6ff", corner: "3D!!",
        marquee: "GET N OR GET OUT • ATOMIC PURPLE • EXPANSION PAK DETECTED • RUMBLE ON •",
        gifTags: ["n64"] },
    genesis: { title: "GENESIS", code: "GEN", sub: "blast processing", badge: "▶",
        skin: "genesis", bg: "#f2f2f4", ink: "#1a0508",
        c1: "#e63946", c2: "#1c1c22", c3: "#c9d6e2",
        caseInk: "#101014", caseFace: "#2a2a30", corner: "FAST!",
        marquee: "GENESIS DOES WHAT NINTENDON'T • BLAST PROCESSING • 6-BUTTON •",
        gifTags: ["gen", "sms"] },
    dreamcast: { title: "DREAMCAST", code: "DC", sub: "it's thinking", badge: "◌",
        skin: "dreamcast", bg: "#f0f8ff", ink: "#062b52",
        c1: "#00a2ff", c2: "#ff8a00", c3: "#0b6ed4",
        caseInk: "#0d4a8a", caseFace: "#ffffff", corner: "DREAM",
        marquee: "IT'S THINKING • VMU BEEPS • 9/9/99 NEVER FORGET • UP TO 4 PLAYERS •",
        gifTags: ["dc"] },
    psx: { title: "PLAYSTATION", code: "PSX", sub: "disc 1 of 3", badge: "△",
        skin: "psx", bg: "#f1f4f7", ink: "#141a24",
        c1: "#2d6ec4", c2: "#9aa5b1", c3: "#ff2d6f",
        caseInk: "#20242c", caseFace: "#d7dde5", corner: "DISC 1",
        marquee: "PLEASE INSERT DISC 2 • MEMORY CARD SLOT 1 OK • TECHNO GRID FOREVER •",
        gifTags: ["ps1"] },
    arcade: { title: "ARCADE", code: "ARCADE", sub: "insert coin", badge: "¤",
        skin: "arcade", bg: "#fffbe8", ink: "#3a2400",
        c1: "#e63946", c2: "#ffd400", c3: "#0038ff",
        caseInk: "#8a1010", caseFace: "#ffe89a", corner: "COIN!",
        marquee: "INSERT COIN • 1 PLAYER START • HIGH SCORE AAA 999999 •",
        gifTags: ["arcade"] },
    gba: { title: "GAME BOY", code: "GBA", sub: "indigo gadget", badge: "▢",
        skin: "gba", bg: "#f2f0fa", ink: "#1e1050",
        c1: "#5b2bd6", c2: "#c0c4d8", c3: "#ffb02e",
        caseInk: "#2a1080", caseFace: "#e6e2fa", corner: "TINY!",
        marquee: "NO BACKLIGHT? SKILL ISSUE • LINK CABLE READY • INDIGO GANG •",
        gifTags: ["gba", "gb", "gbc"] },
    streaming: { title: "STREAMING", code: "STREAM", sub: "over the network", badge: "≈",
        skin: "streaming", bg: "#eefaff", ink: "#053a44",
        c1: "#00b39b", c2: "#00a2ff", c3: "#7cff5a", corner: "LIVE",
        caseInk: "#046b5e", caseFace: "#dffaf5",
        marquee: "PING 6ms • 60 FPS • 25 Mbps • CONNECTION: EXCELLENT :D •",
        gifTags: ["xone", "xsx", "ps5", "overwatch"] },
    steam: { title: "LOCAL STEAM", code: "STEAM", sub: "pc gaming corner", badge: "⚙",
        skin: "steam", bg: "#eef1f6", ink: "#0e1a2b",
        c1: "#3a5a8c", c2: "#7a90ad", c3: "#ffd400", corner: "PC!!",
        caseInk: "#16283f", caseFace: "#dbe4f0",
        marquee: "BIG PICTURE MODE • CONTROLLER DETECTED • PROTON 9 • GABEN BLESS •",
        gifTags: ["pc"] }
};

// Aliases: /roms dir names that differ from the key above.
var ALIAS = { fav: "favorites", gen: "genesis", md: "genesis", megadrive: "genesis",
              dc: "dreamcast", ps1: "psx", playstation: "psx", mame: "arcade",
              gb: "gba", gbc: "gba", stream: "streaming", sfc: "snes" };

var DEFAULT = {
    title: "LIBRARY", code: "LIB", sub: "cab-01", badge: "■", skin: "favorites",
    bg: "#f2f0fa", ink: "#1b0b2e", c1: "#0b3fd1", c2: "#ffd400", c3: "#ff2d8f",
    caseInk: "#1b0b2e", caseFace: "#ffffff", corner: "NEW!",
    marquee: "★ CAB-01 ★ INSERT COIN ★ NO CRT FILTERS WERE HARMED ★",
    gifTags: ["all"]
};

function forShortName(name) {
    var k = String(name || "").toLowerCase();
    if (ALIAS[k]) k = ALIAS[k];
    var s = SYSTEMS[k];
    if (!s) return DEFAULT;
    return s;
}

// Deterministic pick so a system keeps its mascot until you navigate away and
// back. Knuth multiplicative hash, same one the mockup used.
function pickIndex(nonce, len) {
    if (len <= 0) return -1;
    return Math.abs((nonce * 2654435761) % len);
}

// Cover art fallback palette: hash the title into two hues so a game with no
// boxart still gets a stable, distinct-looking case instead of grey.
function coverColors(title) {
    var t = String(title || "");
    var hash = 0;
    for (var i = 0; i < t.length; i++) hash = ((hash << 5) - hash + t.charCodeAt(i)) | 0;
    var h1 = Math.abs(hash) % 360;
    var h2 = (h1 + 40 + (Math.abs(hash >> 8) % 140)) % 360;
    return [h1 / 360, h2 / 360];
}
