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

  readonly property string pinText: panelItem ? panelItem.barPinText : ""
  readonly property real iconSlot: Style.bar.iconSlot
  readonly property real pinGap: Style.space(4)
  // Painted width of the logo slot plus the pinned metric. The bar slot, the
  // click target, and the open-panel underline all follow this, so the metric
  // is not drawn in the margin past an icon-sized module.
  readonly property real contentWidth: iconSlot + (pinText !== "" ? pinGap + pinLabel.implicitWidth : 0)

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

  // Replaces the bar's default mark, which is ~55% of the slot and centered
  // on it. This length is the whole cluster, so the line starts at the logo
  // and ends at the pinned text.
  readonly property real openPanelIndicatorWidth: contentWidth
  readonly property real openPanelIndicatorHeight: Math.max(Style.space(10), Math.round(iconSlot * 0.55))

  implicitWidth: contentWidth
  implicitHeight: button.implicitHeight

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

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: panelItem ? panelItem.tooltipText : "PlanetScale"
    labelVisible: false
    hasVisualContent: true
    fixedWidth: root.contentWidth
    fixedHeight: bar ? bar.barSize : Style.bar.sizeHorizontal
    onPressed: function(buttonCode) { root.handlePress(buttonCode) }

    Row {
      id: cluster
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      spacing: root.pinText !== "" ? root.pinGap : 0
      height: parent.height
      width: root.contentWidth

      Item {
        implicitWidth: root.iconSlot
        implicitHeight: parent.height
        width: root.iconSlot
        height: parent.height

        PlanetScaleLogo {
          anchors.centerIn: parent
          iconSize: Style.bar.iconCanvas
          width: iconSize
          height: iconSize
          opticalScale: 0.75
          color: root.logoColor
        }
      }

      Text {
        id: pinLabel
        visible: root.pinText !== ""
        anchors.verticalCenter: parent.verticalCenter
        text: root.pinText
        textFormat: Text.PlainText
        color: root.logoColor
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.bodySmall
      }
    }
  }
}
