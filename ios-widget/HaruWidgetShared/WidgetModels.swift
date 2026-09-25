import Foundation

struct WidgetItem: Identifiable, Codable, Hashable {
    var id: String
    var title: String
    var kind: String?  // 루틴만 사용: "daily" | "interval" | "weekday" | "monthday"
    var slot: String?  // 매일 루틴만 사용: "" | "morning" | "lunch" | "evening"
}

struct WidgetData: Codable {
    var routines: [WidgetItem]
    var todos: [WidgetItem]
}
