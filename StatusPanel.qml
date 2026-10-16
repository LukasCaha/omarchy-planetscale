import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "lukascaha.planetscale"
  ipcTarget: "lukascaha.planetscale"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  property var status: ({})
  property string tone: "ok"
  property string tooltipText: "PlanetScale"
  property string openUrl: ""
  property string lastGoodTooltip: ""
  property string lastSuccessText: ""
  property string staleNote: ""
  property string credentialNote: ""

  readonly property color foreground: bar ? bar.barForeground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.45)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string fetchScript: scriptPath(Qt.resolvedUrl("fetch.py"))
  readonly property var metricRows: status.metrics || []
  readonly property var deployLines: status.deployLines || []
  readonly property var insightRows: status.insightRows || []
  // Assigned from applyPayload. Binding these to fields on `status` makes Qt
  // re-enter the binding and log a loop.
  property string databaseName: "PlanetScale"
  property string branchName: "main"

  function textOr(value, fallback) {
    if (value === undefined || value === null || String(value) === "") return fallback
    return String(value)
  }

  function syncNames(data) {
    var configuredDatabase = setting("database", "")
    var configuredBranch = setting("branch", "main")
    root.databaseName = root.textOr(data ? data["name"] : "", configuredDatabase ? String(configuredDatabase) : "PlanetScale")
    root.branchName = root.textOr(data ? data["branch"] : "", configuredBranch ? String(configuredBranch) : "main")
  }

  function scriptPath(url) {
    var text = String(url)
    if (text.indexOf("file://") === 0) text = text.substring(7)
    return decodeURIComponent(text)
  }

  function safeUrl(url) {
    var text = String(url || "")
    if (text.indexOf("https://") !== 0) return ""
    if (text.indexOf(" ") >= 0 || text.indexOf("'") >= 0 || text.indexOf("\"") >= 0 || text.indexOf(";") >= 0)
      return ""
    return text
  }

  function refresh() {
    if (fetchProc.running) return
    var org = String(setting("organization", ""))
    var database = String(setting("database", ""))
    var branch = String(setting("branch", "main") || "main")
    if (!org || !database) {
      // The panel is built before the bar injects shell.json settings. An empty
      // object is that startup gap, so keep the last tone instead of flashing red.
      var pending = !root.settings || Object.keys(root.settings).length === 0
      if (pending) return
      applyPayload({
        name: database || "PlanetScale",
        tone: "bad",
        tooltip: "Set organization and database on the plugin entry in ~/.config/omarchy/shell.json.",
        url: "",
        error: "missing settings",
        state: "",
        flags: "",
        schemaRecommendations: null,
        branch: branch,
        deployLines: [],
        metrics: [],
        querySummary: "",
        queryDetail: ""
      })
      return
    }
    fetchProc.command = ["python3", root.fetchScript, org, database, branch]
    fetchProc.running = true
  }

  function applyPayload(data) {
    if (data.error) {
      if (root.lastGoodTooltip !== "") {
        root.staleNote = "Update failed. Last success " + root.lastSuccessText
        return
      }
      root.tone = "bad"
      root.tooltipText = String(data.tooltip || data.error)
      root.openUrl = ""
      root.status = data
      root.syncNames(data)
      root.staleNote = ""
      return
    }
    root.status = data
    root.syncNames(data)
    root.tone = String(data.tone || "ok")
    root.tooltipText = root.databaseName + (data.state ? " · " + data.state : "")
    root.openUrl = root.safeUrl(data.url)
    root.lastGoodTooltip = String(data.tooltip || "")
    root.lastSuccessText = Qt.formatDateTime(new Date(), "HH:mm")
    root.staleNote = ""
  }

  function applyRaw(raw) {
    var text = String(raw || "").trim()
    var line = text
    var splitAt = text.lastIndexOf("\n")
    if (splitAt >= 0) line = text.substring(splitAt + 1)
    try {
      root.applyPayload(JSON.parse(line))
    } catch (e) {
      root.applyPayload({
        name: root.setting("database", "PlanetScale"),
        tone: "bad",
        tooltip: "Could not read PlanetScale status.",
        url: "",
        error: "bad json",
        deployLines: [],
        metrics: []
      })
    }
  }

  function copyCredentials() {
    if (copyProc.running) return
    var org = String(setting("organization", ""))
    var database = String(setting("database", ""))
    var branch = String(setting("branch", "main") || "main")
    if (!org || !database) {
      root.credentialNote = "Set organization and database first."
      return
    }
    root.credentialNote = "Copying…"
    copyProc.command = ["python3", root.fetchScript, org, database, branch, "--copy-credentials"]
    copyProc.running = true
  }

  function applyCopy(raw) {
    var text = String(raw || "").trim()
    var line = text
    var splitAt = text.lastIndexOf("\n")
    if (splitAt >= 0) line = text.substring(splitAt + 1)
    try {
      var data = JSON.parse(line)
      root.credentialNote = String(data.message || "Copied credentials.")
    } catch (e) {
      root.credentialNote = "Could not copy credentials."
    }
  }

  function openDashboard() {
    if (root.openUrl !== "" && root.bar) root.bar.run("xdg-open '" + root.openUrl + "'")
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function open() {
    root.controller.show()
    root.refresh()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open()
  }

  onSettingsChanged: refresh()

  Timer {
    interval: Math.max(30, Number(root.setting("refreshIntervalSec", 60))) * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Process {
    id: fetchProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyRaw(text)
    }
  }

  Process {
    id: copyProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyCopy(text)
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(520))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(key) {
        if (key === "r" || key === "R") root.refresh()
        else if (key === "o" || key === "O") root.openDashboard()
        else if (key === "c" || key === "C") root.copyCredentials()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          x: Style.spacing.hairline
          width: panelFlick.width - Style.spacing.hairline * 2
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            title: root.databaseName
            meta: root.staleNote !== "" ? root.staleNote : String(root.setting("organization", ""))
            detail: root.status.state || ""
            foreground: root.foreground
            fontFamily: root.fontFamily

            iconComponent: Component {
              PlanetScaleLogo {
                iconSize: Style.font.display
                color: root.tone === "bad" ? root.urgent : root.foreground
              }
            }

            trailingControl: Component {
              Row {
                spacing: Style.space(4)

                PanelActionButton {
                  iconText: "󰑐"
                  tooltipText: "Refresh"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  enabled: !fetchProc.running
                  onClicked: root.refresh()
                }

                PanelActionButton {
                  iconText: "󰏌"
                  tooltipText: "Open in browser"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  enabled: root.openUrl !== ""
                  onClicked: root.openDashboard()
                }
              }
            }
          }

          Text {
            visible: root.status.error !== undefined && root.status.error !== null && root.status.error !== "" && root.staleNote === ""
            width: parent.width
            text: root.tooltipText
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }

          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: !root.status.error || root.staleNote !== ""

            PanelSectionHeader {
              text: "STATE"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            StatusRow {
              label: "Branch"
              value: root.branchName
            }
            StatusRow {
              visible: root.status.flags !== ""
              label: "Activity"
              value: root.status.flags || ""
            }
            StatusRow {
              visible: root.status.schemaRecommendations !== null && root.status.schemaRecommendations !== undefined
              label: "Schema recommendations"
              value: root.status.schemaRecommendations === null || root.status.schemaRecommendations === undefined ? "" : String(root.status.schemaRecommendations)
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: !root.status.error || root.staleNote !== ""

            PanelSeparator { width: parent.width; foreground: root.foreground }
            PanelSectionHeader {
              text: "CREDENTIALS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              width: parent.width
              text: "Copy host, user, and password"
              textFormat: Text.PlainText
              topPadding: Style.space(4)
              bottomPadding: Style.space(4)
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.copyCredentials()
              }
            }

            Text {
              visible: root.credentialNote !== ""
              width: parent.width
              text: root.credentialNote
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              color: root.credentialNote.indexOf("Copied") === 0 ? root.foreground : root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Column {
            id: insightsSection
            width: parent.width
            spacing: Style.space(6)
            visible: root.insightRows.length > 0

            PanelSeparator { width: parent.width; foreground: root.foreground }
            PanelSectionHeader {
              text: "INSIGHTS · 24H"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Repeater {
              model: root.insightRows
              delegate: Column {
                required property var modelData
                required property int index
                width: insightsSection.width
                spacing: Style.space(4)

                Item {
                  visible: index > 0
                  width: parent.width
                  height: Style.space(8)
                }

                StatusRow {
                  visible: modelData.p99 !== ""
                  label: "p99"
                  value: modelData.p50 ? modelData.p99 + " · p50 " + modelData.p50 : modelData.p99
                }

                Text {
                  visible: modelData.detail !== ""
                  width: parent.width
                  text: modelData.detail || ""
                  textFormat: Text.PlainText
                  wrapMode: Text.WordWrap
                  horizontalAlignment: Text.AlignRight
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }

                Text {
                  visible: modelData.sql !== ""
                  width: parent.width
                  text: modelData.sql || ""
                  textFormat: Text.PlainText
                  wrapMode: Text.WordWrap
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: !root.status.error || root.staleNote !== ""

            PanelSeparator { width: parent.width; foreground: root.foreground }
            PanelSectionHeader {
              text: "DEPLOYS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Repeater {
              model: root.deployLines
              delegate: Text {
                required property string modelData
                width: column.width
                text: modelData
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: root.metricRows.length > 0

            PanelSeparator { width: parent.width; foreground: root.foreground }
            PanelSectionHeader {
              text: "BRANCH"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Repeater {
              model: root.metricRows
              delegate: StatusRow {
                required property var modelData
                label: modelData.label || ""
                value: modelData.value || ""
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: root.status.querySummary !== "" && root.status.querySummary !== undefined

            PanelSeparator { width: parent.width; foreground: root.foreground }
            PanelSectionHeader {
              text: "QUERIES"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            StatusRow {
              label: "Errors"
              value: root.status.querySummary || ""
            }

            Text {
              visible: root.status.queryDetail !== ""
              width: parent.width
              text: root.status.queryDetail || ""
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Text {
            visible: root.lastSuccessText !== "" && root.staleNote === ""
            width: parent.width
            text: "Updated " + root.lastSuccessText
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignRight
          }
        }
      }
    }
  }

  component StatusRow: Item {
    property string label: ""
    property string value: ""
    width: parent ? parent.width : 0
    implicitHeight: Math.max(labelText.implicitHeight, valueText.implicitHeight)

    Text {
      id: labelText
      anchors.left: parent.left
      anchors.top: parent.top
      width: Math.min(implicitWidth, parent.width * 0.55)
      text: label
      textFormat: Text.PlainText
      elide: Text.ElideRight
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Text {
      id: valueText
      anchors.left: labelText.right
      anchors.leftMargin: Style.space(12)
      anchors.right: parent.right
      anchors.top: parent.top
      text: value
      textFormat: Text.PlainText
      wrapMode: Text.WordWrap
      horizontalAlignment: Text.AlignRight
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
  }
}
