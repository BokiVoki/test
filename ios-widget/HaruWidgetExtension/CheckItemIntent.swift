import AppIntents
import WidgetKit

/// 위젯 안 체크 버튼을 눌렀을 때 실행되는 App Intent (iOS 17+ 인터랙티브 위젯).
/// 루틴/투두 둘 다 이 하나의 intent로 처리 — itemType으로 구분해서 백엔드에 전달.
struct CheckItemIntent: AppIntent {
    static var title: LocalizedStringResource = "체크"

    @Parameter(title: "종류") var itemType: String
    @Parameter(title: "id") var itemId: String

    init() {
        self.itemType = ""
        self.itemId = ""
    }

    init(itemType: String, itemId: String) {
        self.itemType = itemType
        self.itemId = itemId
    }

    func perform() async throws -> some IntentResult {
        await WidgetAPI.check(type: itemType, id: itemId)
        WidgetCenter.shared.reloadAllTimelines()
        return .result()
    }
}
