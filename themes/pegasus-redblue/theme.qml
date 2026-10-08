// pegasus-redblue: UI-1..4. Big highly-visible tiles, red/blue palette,
// Favorites is the boot collection, every screen navigable with
// joystick + accept/back only. Trailers always muted (AUD-2, defensive).
import QtQuick 2.15
import QtMultimedia 5.15   // Pegasus ships Qt 5.15; unversioned import is Qt6-only

FocusScope {
    id: root
    focus: true

    readonly property color cRed: "#e23b3b"
    readonly property color cBlue: "#2b4fd8"
    readonly property color cBg: "#10121a"
    readonly property color cFg: "#eef0f8"

    // Favorites first (UI-2), then everything else in api order.
    function sortedCollections() {
        var favs = [], rest = [];
        for (var i = 0; i < api.collections.count; i++) {
            var c = api.collections.get(i);
            (c.shortName === "favorites" ? favs : rest).push(c);
        }
        return favs.concat(rest);
    }
    property var collections: sortedCollections()
    property int collectionIndex: 0
    readonly property var currentCollection:
        collections.length ? collections[collectionIndex] : null

    Rectangle { anchors.fill: parent; color: cBg }

    // ------- collection strip (top): joystick left/right -------
    Rectangle {
        id: header
        width: parent.width; height: 120
        gradient: Gradient {
            GradientStop { position: 0.0; color: cRed }
            GradientStop { position: 1.0; color: cBlue }
        }
        Text {
            anchors.centerIn: parent
            text: currentCollection ? currentCollection.name : ""
            color: cFg; font.pixelSize: 64; font.bold: true
        }
        Text {
            anchors { right: parent.right; verticalCenter: parent.verticalCenter; rightMargin: 30 }
            text: (collectionIndex + 1) + " / " + collections.length
            color: cFg; font.pixelSize: 28
        }
    }

    // ------- game grid: oversized tiles readable from feet away -------
    GridView {
        id: grid
        anchors { top: header.bottom; left: parent.left; right: parent.right;
                  bottom: parent.bottom; margins: 24 }
        cellWidth: width / 3
        cellHeight: cellWidth * 0.75
        focus: true
        model: currentCollection ? currentCollection.games : null

        delegate: Item {
            width: grid.cellWidth; height: grid.cellHeight
            Rectangle {
                anchors { fill: parent; margins: 12 }
                radius: 14
                color: GridView.isCurrentItem ? cBlue : Qt.darker(cBlue, 2.2)
                border { width: GridView.isCurrentItem ? 6 : 0; color: cRed }

                Image {
                    anchors { fill: parent; margins: 10 }
                    source: modelData.assets.boxFront || modelData.assets.logo || ""
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                }
                // trailer preview: ALWAYS muted (defensive; policy also mutes)
                Loader {
                    anchors.fill: parent
                    active: GridView.isCurrentItem && !!modelData.assets.video
                    sourceComponent: Video {
                        source: modelData.assets.video
                        muted: true
                        autoPlay: true
                        loops: -1
                    }
                }
                Text {
                    anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter;
                              bottomMargin: 10 }
                    width: parent.width - 30
                    text: modelData.title
                    color: cFg; font.pixelSize: 30; font.bold: true
                    elide: Text.ElideRight
                    horizontalAlignment: Text.AlignHCenter
                }
                Text {
                    visible: modelData.favorite
                    anchors { top: parent.top; right: parent.right; margins: 14 }
                    text: "★"; color: cRed; font.pixelSize: 40
                }
            }
        }

        // UI-4: joystick + accept/back (+ one favorite button)
        Keys.onPressed: (event) => {
            if (api.keys.isAccept(event)) {
                event.accepted = true;
                if (event.isAutoRepeat) return;
                if (currentItem) currentCollection.games.get(grid.currentIndex).launch();
            } else if (api.keys.isNextPage(event) ||
                       event.key === Qt.Key_Right && grid.currentIndex % 3 === 2) {
                event.accepted = true;
                if (collections.length > 0)
                    collectionIndex = (collectionIndex + 1) % collections.length;
            } else if (api.keys.isPrevPage(event)) {
                event.accepted = true;
                if (collections.length > 0)
                    collectionIndex = (collectionIndex + collections.length - 1)
                                      % collections.length;
            } else if (api.keys.isFilters(event)) {   // favorite: Y, panel 2
                // NOTE: toggles Pegasus's internal favorite flag (star badge
                // only). The authoritative favorites list lives in
                // state/library/favorites.yaml (arcadectl favorites ...);
                // the generated Favorites collection follows state, not this
                // flag. Known divergence — see HARDWARE-TODO.
                event.accepted = true;
                var g = currentCollection.games.get(grid.currentIndex);
                g.favorite = !g.favorite;
            }
        }
    }
}
