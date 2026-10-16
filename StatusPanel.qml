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
  property string tokenNote: ""
  property var pinIds: ["p99"]
  property string metricPeriod: "1h"
  property string chartTab: "latency"
  readonly property var periodOptions: ["15m", "1h", "6h", "1d", "7d"]
  readonly property color warnColor: "#e0b341"
  readonly property var chartList: {
    var charts = root.status ? root.status.charts : []
    return charts && charts.length ? charts : []
  }
  readonly property var activeChart: {
    var charts = root.chartList
    for (var i = 0; i < charts.length; i++) {
      if (charts[i] && charts[i].id === root.chartTab) return charts[i]
    }
    return charts.length > 0 ? charts[0] : null
  }
  readonly property string activeChartId: activeChart && activeChart.id ? activeChart.id : ""
  readonly property var activeSeries: activeChart && activeChart.series ? activeChart.series : []

  readonly property color foreground: bar ? bar.barForeground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.45)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string fetchScript: scriptPath(Qt.resolvedUrl("fetch.py"))
  readonly property var deployLines: status.deployLines || []
  readonly property var numberRows: status.numbers || []
  readonly property bool needsToken: status.needsToken === true
  readonly property bool showBody: {
    var problem = root.status.error
    return problem === undefined || problem === null || problem === "" || root.staleNote !== ""
  }
  readonly property bool showActivity: {
    var flags = String(root.status.flags || "")
    return flags !== "" && flags.indexOf("No resize") !== 0
  }
  readonly property string barPinText: {
    var rows = root.numberRows
    var parts = []
    for (var p = 0; p < root.pinIds.length; p++) {
      for (var i = 0; i < rows.length; i++) {
        if (rows[i] && rows[i].id === root.pinIds[p] && rows[i].pin)
          parts.push(rows[i].pin)
      }
    }
    return parts.join("  ")
  }
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

  function readPinIds() {
    var source = root.settings || {}
    var metrics = source.barMetrics
    if (metrics !== undefined && metrics !== null) {
      var chosen = []
      var length = metrics.length || 0
      for (var i = 0; i < length && chosen.length < 2; i++) {
        if (metrics[i]) chosen.push(String(metrics[i]))
      }
      return chosen
    }
    if (source.barMetric) return [String(source.barMetric)]
    return ["p99"]
  }

  function readPeriod() {
    var value = String((root.settings || {}).metricPeriod || "")
    for (var i = 0; i < root.periodOptions.length; i++) {
      if (root.periodOptions[i] === value) return value
    }
    return "1h"
  }

  function chartColor(seriesId) {
    if (seriesId === "p99" || seriesId === "qps" || seriesId === "cpu") return Color.accent
    if (seriesId === "p95" || seriesId === "memory") return root.warnColor
    return root.foreground
  }

  function chartReadout(series, index) {
    if (!series) return ""
    var points = series.points || []
    var chosen = index
    if (chosen < 0) chosen = points.length - 1
    if (chosen < 0 || chosen >= points.length) return ""
    var value = points[chosen]
    if (value === null || value === undefined || value === "") return ""
    var unit = root.activeChart && root.activeChart.unit ? root.activeChart.unit : ""
    return String(value) + unit
  }

  function rowsIn(group) {
    var rows = root.numberRows
    var out = []
    for (var i = 0; i < rows.length; i++) {
      if (rows[i] && rows[i].group === group) out.push(rows[i])
    }
    return out
  }

  function isPinned(metricId) {
    for (var i = 0; i < root.pinIds.length; i++) {
      if (root.pinIds[i] === metricId) return true
    }
    return false
  }

  function persistSettings(values) {
    var source = root.settings || {}
    if (!source.database && !source.organization) return
    var entry = { id: root.moduleName }
    for (var key in source) {
      if (key !== "id" && key !== "barMetric") entry[key] = source[key]
    }
    for (var name in values) entry[name] = values[name]
    root.settings = entry
    if (root.hostWidget && "settings" in root.hostWidget) root.hostWidget.settings = entry
    if (root.bar && root.bar.shell && typeof root.bar.shell.updateEntryInline === "function")
      root.bar.shell.updateEntryInline(root.moduleName, entry)
  }

  function persistPins(ids) {
    root.persistSettings({ barMetrics: ids })
  }

  function setPeriod(value) {
    if (!value || value === root.metricPeriod) return
    root.metricPeriod = value
    root.persistSettings({ metricPeriod: value })
    root.refresh()
  }

  function togglePin(metricId) {
    if (!metricId) return
    var pin = ""
    for (var i = 0; i < root.numberRows.length; i++) {
      if (root.numberRows[i] && root.numberRows[i].id === metricId)
        pin = root.numberRows[i].pin || ""
    }
    if (!pin) return
    var next = []
    var removed = false
    var current = root.pinIds
    var length = current ? (current.length || 0) : 0
    for (var i = 0; i < length; i++) {
      if (String(current[i]) === String(metricId)) removed = true
      else if (current[i]) next.push(String(current[i]))
    }
    if (!removed) {
      if (next.length >= 2) next.shift()
      next.push(String(metricId))
    }
    root.pinIds = next
    root.persistPins(next)
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
    fetchProc.command = ["python3", root.fetchScript, org, database, branch, root.metricPeriod]
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

  function saveToken() {
    if (saveProc.running) return
    var value = String(tokenField.text || "").trim()
    if (!value) {
      root.tokenNote = "Paste a service token first."
      return
    }
    root.tokenNote = "Saving…"
    saveProc.environment = { "PS_PLUGIN_TOKEN_VALUE": value }
    saveProc.command = ["python3", root.fetchScript, "--save-token"]
    saveProc.running = true
  }

  function applySave(raw) {
    saveProc.environment = ({})
    var text = String(raw || "").trim()
    var line = text
    var splitAt = text.lastIndexOf("\n")
    if (splitAt >= 0) line = text.substring(splitAt + 1)
    try {
      var data = JSON.parse(line)
      root.tokenNote = String(data.message || "Saved the service token.")
      if (data.ok === true) {
        tokenField.text = ""
        root.refresh()
      }
    } catch (e) {
      root.tokenNote = "Could not save the service token."
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

  onSettingsChanged: {
    root.pinIds = root.readPinIds()
    root.metricPeriod = root.readPeriod()
    root.refresh()
  }

  Component.onCompleted: {
    root.pinIds = root.readPinIds()
    root.metricPeriod = root.readPeriod()
  }

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
    id: saveProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applySave(text)
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(520))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: tokenField.activeFocus
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(key) {
        if (key === "r" || key === "R") root.refresh()
        else if (key === "o" || key === "O") root.openDashboard()
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
          spacing: Style.space(8)

          PanelHero {
            width: parent.width
            title: root.databaseName
            meta: {
              if (root.staleNote !== "") return root.staleNote
              var org = String(root.setting("organization", ""))
              if (root.lastSuccessText !== "" && !root.needsToken)
                return org !== "" ? org + " · " + root.lastSuccessText : root.lastSuccessText
              return org
            }
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
            visible: !root.needsToken && root.status.error !== undefined && root.status.error !== null && root.status.error !== "" && root.staleNote === ""
            width: parent.width
            text: root.tooltipText
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }

          Column {
            visible: root.needsToken
            width: parent.width
            spacing: Style.space(6)

            PanelSectionHeader {
              text: "SERVICE TOKEN"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              width: parent.width
              text: root.tooltipText
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            TextField {
              id: tokenField
              width: parent.width
              password: true
              placeholderText: "TOKEN_ID:TOKEN"
              foreground: root.foreground
              font.family: root.fontFamily
              verticalPadding: Style.space(4)
              onAccepted: root.saveToken()
              Keys.onEscapePressed: function(event) {
                focus = false
                keyCatcher.forceActiveFocus()
                event.accepted = true
              }
            }

            Text {
              visible: root.tokenNote !== ""
              width: parent.width
              text: root.tokenNote
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(4)
            visible: root.showBody

            Flow {
              width: parent.width
              spacing: Style.space(8)

              Text {
                text: root.branchName
                textFormat: Text.PlainText
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Repeater {
                model: root.rowsIn("state")
                delegate: MetricChip {}
              }

              Text {
                visible: root.showActivity
                text: root.status.flags || ""
                textFormat: Text.PlainText
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(4)
            visible: root.rowsIn("insights").length > 0

            PanelSeparator { width: parent.width; foreground: root.foreground }
            PanelSectionHeader {
              text: "INSIGHTS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Row {
              width: parent.width
              spacing: Style.space(8)

              Repeater {
                model: root.periodOptions
                delegate: Text {
                  required property string modelData
                  text: modelData
                  textFormat: Text.PlainText
                  color: modelData === root.metricPeriod ? root.foreground : root.dim
                  font.bold: modelData === root.metricPeriod
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall

                  MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.setPeriod(modelData)
                  }
                }
              }
            }

            Column {
              id: chartBlock
              width: parent.width
              spacing: Style.space(4)
              visible: root.chartList.length > 0

              Row {
                width: parent.width
                spacing: Style.space(8)

                Repeater {
                  model: root.chartList
                  delegate: Text {
                    required property var modelData
                    text: modelData.label || ""
                    textFormat: Text.PlainText
                    color: modelData.id === root.activeChartId ? root.foreground : root.dim
                    font.bold: modelData.id === root.activeChartId
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall

                    MouseArea {
                      anchors.fill: parent
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.chartTab = modelData.id || ""
                    }
                  }
                }
              }

              ChartPlot {
                id: plot
                width: parent.width
                height: Style.space(72)
                chart: root.activeChart
                foreground: root.foreground
              }

              Flow {
                width: parent.width
                spacing: Style.space(8)
                visible: root.activeSeries.length > 0

                Repeater {
                  model: root.activeSeries
                  delegate: Row {
                    required property var modelData
                    spacing: Style.space(4)

                    Rectangle {
                      width: Style.space(10)
                      height: Style.space(2)
                      radius: height / 2
                      anchors.verticalCenter: parent.verticalCenter
                      color: root.chartColor(modelData.id || "")
                    }

                    Text {
                      text: {
                        var label = modelData.label || ""
                        var value = root.chartReadout(modelData, plot.hoverIndex)
                        return plot.hoverIndex < 0 || value === "" ? label : label + " " + value
                      }
                      textFormat: Text.PlainText
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      anchors.verticalCenter: parent.verticalCenter
                    }
                  }
                }
              }
            }

            NumberFlow { group: "insights" }
          }

          Column {
            width: parent.width
            spacing: Style.space(4)
            visible: root.showBody

            PanelSeparator { width: parent.width; foreground: root.foreground }

            Row {
              width: parent.width
              spacing: Style.space(8)

              PanelSectionHeader {
                text: "DEPLOYS"
                foreground: root.foreground
                fontFamily: root.fontFamily
              }
            }

            Repeater {
              model: root.deployLines
              delegate: Text {
                required property string modelData
                width: column.width
                text: modelData
                textFormat: Text.PlainText
                elide: Text.ElideRight
                maximumLineCount: 1
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(4)
            visible: root.rowsIn("branch").length > 0

            PanelSeparator { width: parent.width; foreground: root.foreground }
            PanelSectionHeader {
              text: "BRANCH"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            NumberFlow { group: "branch" }
          }

          Column {
            width: parent.width
            spacing: Style.space(4)
            visible: root.rowsIn("queries").length > 0

            PanelSeparator { width: parent.width; foreground: root.foreground }
            PanelSectionHeader {
              text: "QUERIES"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            NumberFlow { group: "queries" }

            Text {
              visible: root.status.queryDetail !== "" && root.status.queryDetail !== undefined
              width: parent.width
              text: root.status.queryDetail || ""
              textFormat: Text.PlainText
              elide: Text.ElideRight
              maximumLineCount: 1
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }
        }
      }
    }
  }

  component MetricChip: Text {
    id: chip
    required property var modelData
    readonly property bool pinned: root.isPinned(modelData && modelData.id ? modelData.id : "")
    readonly property string pinValue: modelData && modelData.pin ? modelData.pin : ""

    text: (pinned ? "● " : "") + (modelData.label || "") + " " + (modelData.value || "")
    textFormat: Text.PlainText
    color: pinValue !== "" ? root.foreground : root.dim
    font.bold: pinned
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall

    MouseArea {
      anchors.fill: parent
      enabled: chip.pinValue !== ""
      cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
      onClicked: root.togglePin(modelData.id || "")
    }
  }

  component ChartPlot: Canvas {
    id: plot
    property var chart: null
    property color foreground: "#888888"
    property int hoverIndex: -1

    readonly property int pointCount: {
      var series = chart && chart.series ? chart.series : []
      var count = 0
      for (var i = 0; i < series.length; i++) {
        var points = series[i] && series[i].points ? series[i].points : []
        if (points.length > count) count = points.length
      }
      return count
    }

    function sample(value) {
      if (value === null || value === undefined || value === "") return NaN
      var number = Number(value)
      return isNaN(number) ? NaN : number
    }

    function xAt(index, count) {
      if (count <= 1) return width / 2
      return index * (width - 1) / (count - 1)
    }

    onChartChanged: {
      hoverIndex = -1
      requestPaint()
    }
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onForegroundChanged: requestPaint()
    onHoverIndexChanged: requestPaint()

    onPaint: {
      var ctx = getContext("2d")
      ctx.clearRect(0, 0, width, height)
      var series = chart && chart.series ? chart.series : []
      var count = plot.pointCount
      if (!series.length || count === 0 || width < 2 || height < 2) return

      var maxValue = 0
      for (var s = 0; s < series.length; s++) {
        var points = series[s].points || []
        for (var i = 0; i < points.length; i++) {
          var value = plot.sample(points[i])
          if (!isNaN(value) && value > maxValue) maxValue = value
        }
      }
      if (chart.scale === "percent") maxValue = Math.max(100, maxValue)
      if (maxValue <= 0) maxValue = 1

      function yAt(value) {
        return height - 2 - (value / maxValue) * (height - 4)
      }

      ctx.strokeStyle = Qt.rgba(plot.foreground.r, plot.foreground.g, plot.foreground.b, 0.16)
      ctx.lineWidth = 1
      for (var grid = 0; grid <= 2; grid++) {
        var gy = Math.round(yAt(maxValue * grid / 2)) + 0.5
        ctx.beginPath()
        ctx.moveTo(0, gy)
        ctx.lineTo(width, gy)
        ctx.stroke()
      }

      ctx.lineJoin = "round"
      ctx.lineCap = "round"
      for (var seriesIndex = 0; seriesIndex < series.length; seriesIndex++) {
        var line = series[seriesIndex].points || []
        ctx.beginPath()
        ctx.strokeStyle = root.chartColor(series[seriesIndex].id || "")
        ctx.lineWidth = seriesIndex === series.length - 1 ? 1.75 : 1.25
        var started = false
        for (var point = 0; point < line.length; point++) {
          var sample = plot.sample(line[point])
          if (isNaN(sample)) {
            started = false
            continue
          }
          var x = plot.xAt(point, count)
          var y = yAt(sample)
          if (!started) {
            ctx.moveTo(x, y)
            started = true
          } else {
            ctx.lineTo(x, y)
          }
        }
        ctx.stroke()
      }

      if (plot.hoverIndex < 0 || plot.hoverIndex >= count) return
      var hoverX = plot.xAt(plot.hoverIndex, count)
      ctx.strokeStyle = Qt.rgba(plot.foreground.r, plot.foreground.g, plot.foreground.b, 0.35)
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(hoverX, 0)
      ctx.lineTo(hoverX, height)
      ctx.stroke()
      for (var mark = 0; mark < series.length; mark++) {
        var marked = series[mark].points || []
        var markedValue = plot.sample(marked[plot.hoverIndex])
        if (isNaN(markedValue)) continue
        ctx.fillStyle = root.chartColor(series[mark].id || "")
        ctx.beginPath()
        ctx.arc(hoverX, yAt(markedValue), 2.5, 0, Math.PI * 2)
        ctx.fill()
      }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      enabled: plot.pointCount > 0
      onPositionChanged: function(mouse) {
        var count = plot.pointCount
        if (count <= 1) {
          plot.hoverIndex = 0
          return
        }
        var index = Math.round(mouse.x / Math.max(1, width) * (count - 1))
        if (index < 0) index = 0
        if (index > count - 1) index = count - 1
        plot.hoverIndex = index
      }
      onExited: plot.hoverIndex = -1
    }
  }

  component NumberFlow: Flow {
    property string group: ""
    width: parent ? parent.width : 0
    spacing: Style.space(8)

    Repeater {
      model: root.rowsIn(group)
      delegate: MetricChip {}
    }
  }
}
