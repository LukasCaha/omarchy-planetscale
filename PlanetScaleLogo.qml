import QtQuick
import QtQuick.Shapes
import qs.Commons

// PlanetScale mark, painted in the bar foreground so it follows the theme.
Item {
  id: root

  property real iconSize: Style.bar.iconCanvas
  property real opticalScale: 1
  property color color: Color.foreground

  implicitWidth: iconSize
  implicitHeight: iconSize

  Shape {
    anchors.fill: parent
    antialiasing: true
    preferredRendererType: Shape.CurveRenderer

    ShapePath {
      fillColor: root.color
      strokeWidth: 0
      PathSvg {
        path: "M0 12C0 5.373 5.373 0 12 0c4.873 0 9.067 2.904 10.947 7.077l-15.87 15.87a11.981 11.981 0 0 1-1.935-1.099L14.99 12H12l-8.485 8.485A11.962 11.962 0 0 1 0 12Zm12.004 12L24 12.004C23.998 18.628 18.628 23.998 12.004 24Z"
      }
    }

    transform: [
      Scale {
        origin.x: 0
        origin.y: 0
        xScale: root.width > 0 ? root.width / 24 : 1
        yScale: root.height > 0 ? root.height / 24 : 1
      },
      Scale {
        origin.x: root.width / 2
        origin.y: root.height / 2
        xScale: root.opticalScale
        yScale: root.opticalScale
      }
    ]
  }
}
