import SwiftUI
import WidgetKit

struct HaruWidget: Widget {
    let kind: String = "HaruWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: HaruProvider()) { entry in
            HaruWidgetView(entry: entry)
        }
        .configurationDisplayName("하루")
        .description("오늘 체크할 루틴과 투두")
        .supportedFamilies([.accessoryRectangular, .systemMedium])
    }
}
