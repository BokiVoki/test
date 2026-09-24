import Foundation

struct WidgetItem: Identifiable, Codable, Hashable {
    var id: String
    var title: String
    var kind: String?  // 루틴만 사용: "daily" | "interval" | "weekday" | "monthday"
}

struct WidgetData: Codable {
    var routines: [WidgetItem]
    var todos: [WidgetItem]
}
