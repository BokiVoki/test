import WidgetKit

struct HaruEntry: TimelineEntry {
    let date: Date
    let routines: [WidgetItem]
    let todos: [WidgetItem]
}

struct HaruProvider: TimelineProvider {
    func placeholder(in context: Context) -> HaruEntry {
        HaruEntry(
            date: Date(),
            routines: [WidgetItem(id: "p1", title: "물 8잔 마시기", kind: "daily")],
            todos: [WidgetItem(id: "p2", title: "우유 사기", kind: nil)]
        )
    }

    func getSnapshot(in context: Context, completion: @escaping (HaruEntry) -> Void) {
        if context.isPreview {
            completion(placeholder(in: context))
            return
        }
        Task {
            let data = await WidgetAPI.fetchData()
            completion(HaruEntry(date: Date(), routines: data.routines, todos: data.todos))
        }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<HaruEntry>) -> Void) {
        Task {
            let data = await WidgetAPI.fetchData()
            let entry = HaruEntry(date: Date(), routines: data.routines, todos: data.todos)
            // 30분마다 자동 새로고침 시도(iOS가 배터리 상황에 따라 늦출 수 있음).
            // 체크 버튼을 누르면 CheckItemIntent가 reloadAllTimelines()로 즉시 갱신하므로
            // 이 주기는 "아무것도 안 눌렀을 때"의 보조 새로고침일 뿐.
            let next = Calendar.current.date(byAdding: .minute, value: 30, to: Date()) ?? Date().addingTimeInterval(1800)
            completion(Timeline(entries: [entry], policy: .after(next)))
        }
    }
}
