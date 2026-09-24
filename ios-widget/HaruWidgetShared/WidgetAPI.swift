import Foundation

// Supabase Edge Function(widget-data)을 호출하는 얇은 네트워킹 레이어.
// 로그인 세션이 없는 위젯/미니앱이라 Supabase JWT 대신 고정 토큰으로만 인증한다
// (개인 단일사용자 앱이라 이 정도 보안 수준이면 충분 — DB 전체 키가 아니라
// 이 좁은 엔드포인트 하나에만 통하는 토큰이라 블라스트 반경이 작음).
enum WidgetAPI {
    static let base = "https://mfgiesampazjzgfliuje.supabase.co/functions/v1/widget-data"
    static let token = "941f03b3326d9741eace3df11fe7217fdfbe7621b4607484"

    static func fetchData() async -> WidgetData {
        guard let url = URL(string: base) else { return WidgetData(routines: [], todos: []) }
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.setValue(token, forHTTPHeaderField: "x-widget-token")
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            return try JSONDecoder().decode(WidgetData.self, from: data)
        } catch {
            return WidgetData(routines: [], todos: [])
        }
    }

    /// type: "routine" | "todo"
    static func check(type: String, id: String) async {
        guard let url = URL(string: base) else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue(token, forHTTPHeaderField: "x-widget-token")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: String] = ["type": type, "action": "check", "id": id]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: req)
    }

    static func addTodo(title: String) async -> Bool {
        guard let url = URL(string: base) else { return false }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue(token, forHTTPHeaderField: "x-widget-token")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: String] = ["type": "todo", "action": "add", "title": title]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse { return http.statusCode == 200 }
            return false
        } catch {
            return false
        }
    }
}
