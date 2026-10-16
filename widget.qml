import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "lukascaha.planetscale"

  readonly property var panelItem: panelLoader.item
  readonly property bool opened: panelItem ? panelItem.opened === true : false
  readonly property bool popoutSwitchClosing: panelItem ? panelItem.popoutSwitchClosing === true : false
  readonly property color warnColor: "#e0b341"
  readonly property string tone: panelItem ? panelItem.tone : "ok"
  readonly property color logoColor: tone === "bad"
    ? (bar ? bar.urgent : Color.urgent)
    : tone === "warn"
      ? warnColor
      : (bar ? bar.barForeground : Color.foreground)

  readonly property string barMetric: {
    var value = setting("barMetric", "p99")
    return value ? String(value) : "p99"
  }
  readonly property string pinText: {
    var rows = panelItem && panelItem.status ? panelItem.status.insightRows : null
    if (!rows) return ""
    for (var i = 0; i < rows.length; i++) {
      if (rows[i] && rows[i].id === barMetric) return rows[i].pin || ""
    }
    return ""
  }

  function open() { if (panelItem) panelItem.open() }
  function close() { if (panelItem) panelItem.close() }
  function toggle() { if (panelItem) panelItem.toggle() }
  function closeForPopoutSwitch() { if (panelItem) panelItem.closeForPopoutSwitch() }
  function refresh() { if (panelItem) panelItem.refresh() }
  function handlePress(buttonCode) {
    if (buttonCode === Qt.RightButton) {
      if (panelItem) panelItem.openDashboard()
    } else if (buttonCode === Qt.MiddleButton) root.refresh()
    else root.toggle()
  }

  function injectPanel() {
    var target = panelItem
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = cluster
    if ("hostWidget" in target) target.hostWidget = root
  }

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  implicitWidth: cluster.implicitWidth
  implicitHeight: cluster.implicitHeight

  IpcHandler {
    target: "lukascaha.planetscale"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): void { root.refresh() }
  }

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("StatusPanel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  Row {
    id: cluster
    spacing: Style.space(4)

    BarIconButton {
      id: iconButton
      bar: root.bar
      tooltipText: panelItem ? panelItem.tooltipText : "PlanetScale"
      iconComponent: Component {
        PlanetScaleLogo {
          anchors.centerIn: parent
          iconSize: Style.bar.iconCanvas
          width: iconSize
          height: iconSize
          opticalScale: 0.75
          color: root.logoColor
        }
      }
      onPressed: function(buttonCode) { root.handlePress(buttonCode) }
    }

    Item {
      visible: root.pinText !== ""
      width: pinLabel.implicitWidth
      height: iconButton.implicitHeight

      Text {
        id: pinLabel
        anchors.verticalCenter: parent.verticalCenter
        text: root.pinText
        textFormat: Text.PlainText
        color: root.logoColor
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.bodySmall
      }

      MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
        cursorShape: Qt.PointingHandCursor
        onPressed: function(mouse) { root.handlePress(mouse.button) }
      }
    }
  }
}
