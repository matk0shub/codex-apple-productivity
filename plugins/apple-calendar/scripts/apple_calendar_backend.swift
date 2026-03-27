import Foundation
import EventKit

enum CalendarBackendError: Error, LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let message):
            return message
        }
    }
}

let isoFormatterWithFractional: ISO8601DateFormatter = {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter
}()

let isoFormatter: ISO8601DateFormatter = {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime]
    return formatter
}()

func isoString(_ date: Date?) -> String? {
    guard let date else { return nil }
    return isoFormatterWithFractional.string(from: date)
}

func parseISODate(_ value: String) throws -> Date {
    if let date = isoFormatterWithFractional.date(from: value) ?? isoFormatter.date(from: value) {
        return date
    }
    throw CalendarBackendError.message("Invalid ISO datetime: \(value)")
}

func requireArg(_ name: String, in args: [String]) throws -> String {
    guard let idx = args.firstIndex(of: name), idx + 1 < args.count else {
        throw CalendarBackendError.message("Missing required argument \(name)")
    }
    return args[idx + 1]
}

func optionalArg(_ name: String, in args: [String]) -> String? {
    guard let idx = args.firstIndex(of: name), idx + 1 < args.count else {
        return nil
    }
    return args[idx + 1]
}

func boolArg(_ name: String, in args: [String], default defaultValue: Bool = false) -> Bool {
    guard let value = optionalArg(name, in: args) else { return defaultValue }
    return value == "1" || value.lowercased() == "true"
}

func intArg(_ name: String, in args: [String], default defaultValue: Int) -> Int {
    guard let value = optionalArg(name, in: args), let parsed = Int(value) else { return defaultValue }
    return parsed
}

func jsonArrayArg(_ name: String, in args: [String]) throws -> [String] {
    let raw = try requireArg(name, in: args)
    guard let data = raw.data(using: .utf8),
          let decoded = try JSONSerialization.jsonObject(with: data) as? [String] else {
        throw CalendarBackendError.message("Expected JSON array for \(name)")
    }
    return decoded
}

func requestCalendarAccess(store: EKEventStore) throws {
    let sem = DispatchSemaphore(value: 0)
    var granted = false
    var accessError: Error?
    if #available(macOS 14.0, *) {
        store.requestFullAccessToEvents { ok, err in
            granted = ok
            accessError = err
            sem.signal()
        }
    } else {
        store.requestAccess(to: .event) { ok, err in
            granted = ok
            accessError = err
            sem.signal()
        }
    }
    _ = sem.wait(timeout: .now() + 30)
    if let accessError { throw accessError }
    if !granted {
        throw CalendarBackendError.message("Calendar access was not granted.")
    }
}

func selectedCalendars(store: EKEventStore, names: [String]) -> [EKCalendar] {
    let calendars = store.calendars(for: .event)
    return calendars.filter { names.contains($0.title) }
}

func weekdayCode(_ day: EKWeekday) -> String {
    switch day {
    case .sunday: return "SU"
    case .monday: return "MO"
    case .tuesday: return "TU"
    case .wednesday: return "WE"
    case .thursday: return "TH"
    case .friday: return "FR"
    case .saturday: return "SA"
    @unknown default: return "MO"
    }
}

func recurrenceRecord(_ event: EKEvent) -> [String: Any]? {
    guard let rule = event.recurrenceRules?.first else { return nil }
    var payload: [String: Any] = [
        "interval": rule.interval
    ]
    switch rule.frequency {
    case .daily:
        payload["frequency"] = "daily"
    case .weekly:
        payload["frequency"] = "weekly"
    case .monthly:
        payload["frequency"] = "monthly"
    case .yearly:
        payload["frequency"] = "yearly"
    @unknown default:
        payload["frequency"] = "daily"
    }
    if let weekdays = rule.daysOfTheWeek {
        payload["weekdays"] = weekdays.map { weekdayCode($0.dayOfTheWeek) }
    }
    return payload
}

func recurrenceRule(from args: [String]) throws -> EKRecurrenceRule? {
    guard let repeatValue = optionalArg("--repeat", in: args) else { return nil }
    let interval = max(1, intArg("--repeat-interval", in: args, default: 1))
    let weekdayMap: [String: EKWeekday] = [
        "SU": .sunday,
        "MO": .monday,
        "TU": .tuesday,
        "WE": .wednesday,
        "TH": .thursday,
        "FR": .friday,
        "SA": .saturday,
    ]
    switch repeatValue.lowercased() {
    case "daily":
        return EKRecurrenceRule(
            recurrenceWith: .daily,
            interval: interval,
            end: nil
        )
    case "weekly":
        let weekdayValue = optionalArg("--repeat-weekdays", in: args) ?? ""
        let weekdays = weekdayValue
            .split(separator: ",")
            .compactMap { weekdayMap[$0.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()] }
            .map { EKRecurrenceDayOfWeek($0) }
        return EKRecurrenceRule(
            recurrenceWith: .weekly,
            interval: interval,
            daysOfTheWeek: weekdays.isEmpty ? nil : weekdays,
            daysOfTheMonth: nil,
            monthsOfTheYear: nil,
            weeksOfTheYear: nil,
            daysOfTheYear: nil,
            setPositions: nil,
            end: nil
        )
    default:
        throw CalendarBackendError.message("Unsupported repeat value: \(repeatValue)")
    }
}

func alarmOffsets(from args: [String]) throws -> [Int]? {
    guard let raw = optionalArg("--alarms-json", in: args) else { return nil }
    guard let data = raw.data(using: .utf8),
          let decoded = try JSONSerialization.jsonObject(with: data) as? [Int] else {
        throw CalendarBackendError.message("Expected JSON array for --alarms-json")
    }
    return decoded
}

func eventRecord(_ event: EKEvent) -> [String: Any] {
    let alarmOffsets = (event.alarms ?? [])
        .compactMap { alarm -> Int? in
            guard let offset = alarm.relativeOffset as Double? else { return nil }
            return Int(abs(offset) / 60.0)
        }
        .sorted()

    return [
        "calendar": event.calendar.title,
        "uid": event.eventIdentifier as Any,
        "id": event.eventIdentifier as Any,
        "externalId": event.calendarItemExternalIdentifier as Any,
        "summary": event.title ?? "",
        "startDate": isoString(event.startDate) as Any,
        "endDate": isoString(event.endDate) as Any,
        "location": event.location as Any,
        "description": event.notes as Any,
        "remindersMinutesBefore": alarmOffsets,
        "alldayEvent": event.isAllDay,
        "status": event.status.rawValue,
        "recurrence": recurrenceRecord(event) as Any
    ]
}

func emitJSON(_ payload: Any) throws {
    let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted])
    guard let text = String(data: data, encoding: .utf8) else {
        throw CalendarBackendError.message("Failed to encode JSON output.")
    }
    print(text)
}

func eventByID(store: EKEventStore, id: String) -> EKEvent? {
    store.event(withIdentifier: id)
}

func applyAlarms(_ offsets: [Int]?, to event: EKEvent) {
    guard let offsets else { return }
    event.alarms = offsets.map { EKAlarm(relativeOffset: -Double(abs($0) * 60)) }
}

let store = EKEventStore()

do {
    try requestCalendarAccess(store: store)
    let args = Array(CommandLine.arguments.dropFirst())
    guard let command = args.first else {
        throw CalendarBackendError.message("Missing command.")
    }

    switch command {
    case "list-calendars":
        try emitJSON(store.calendars(for: .event).map { $0.title })

    case "list-events":
        let calendarNames = try jsonArrayArg("--calendars-json", in: args)
        let start = try parseISODate(requireArg("--start-iso", in: args))
        let end = try parseISODate(requireArg("--end-iso", in: args))
        let calendars = selectedCalendars(store: store, names: calendarNames)
        let events = store.events(matching: store.predicateForEvents(withStart: start, end: end, calendars: calendars))
            .sorted { $0.startDate < $1.startDate }
            .map(eventRecord)
        try emitJSON(events)

    case "get-event":
        let eventID = try requireArg("--id", in: args)
        guard let event = eventByID(store: store, id: eventID) else {
            throw CalendarBackendError.message("Event not found: \(eventID)")
        }
        try emitJSON(eventRecord(event))

    case "add":
        let calendarName = try requireArg("--calendar", in: args)
        let title = try requireArg("--title", in: args)
        let start = try parseISODate(requireArg("--start-iso", in: args))
        let end = try parseISODate(requireArg("--end-iso", in: args))
        let location = optionalArg("--location", in: args)
        let notes = optionalArg("--notes", in: args)
        let alarms = try alarmOffsets(from: args)
        guard let calendar = store.calendars(for: .event).first(where: { $0.title == calendarName }) else {
            throw CalendarBackendError.message("Calendar not found: \(calendarName)")
        }
        let event = EKEvent(eventStore: store)
        event.calendar = calendar
        event.title = title
        event.startDate = start
        event.endDate = end
        event.location = location
        event.notes = notes
        applyAlarms(alarms, to: event)
        event.recurrenceRules = try recurrenceRule(from: args).map { [$0] }
        try store.save(event, span: .thisEvent, commit: true)
        var payload = eventRecord(event)
        payload["created"] = true
        try emitJSON(payload)

    case "update":
        let eventID = try requireArg("--id", in: args)
        guard let event = eventByID(store: store, id: eventID) else {
            throw CalendarBackendError.message("Event not found: \(eventID)")
        }
        if let title = optionalArg("--title", in: args) { event.title = title }
        if let startValue = optionalArg("--start-iso", in: args) { event.startDate = try parseISODate(startValue) }
        if let endValue = optionalArg("--end-iso", in: args) { event.endDate = try parseISODate(endValue) }
        if let location = optionalArg("--location", in: args) { event.location = location }
        if let notes = optionalArg("--notes", in: args) { event.notes = notes }
        if let moveToCalendar = optionalArg("--move-to-calendar", in: args) {
            guard let calendar = store.calendars(for: .event).first(where: { $0.title == moveToCalendar }) else {
                throw CalendarBackendError.message("Calendar not found: \(moveToCalendar)")
            }
            event.calendar = calendar
        }
        if boolArg("--clear-alarms", in: args) {
            event.alarms = nil
        } else if let alarms = try alarmOffsets(from: args) {
            applyAlarms(alarms, to: event)
        }
        if boolArg("--clear-repeat", in: args) {
            event.recurrenceRules = nil
        } else if let rule = try recurrenceRule(from: args) {
            event.recurrenceRules = [rule]
        }
        let span: EKSpan = (event.recurrenceRules?.isEmpty == false) ? .futureEvents : .thisEvent
        try store.save(event, span: span, commit: true)
        var payload = eventRecord(event)
        payload["updated"] = true
        try emitJSON(payload)

    case "delete":
        let eventID = try requireArg("--id", in: args)
        guard let event = eventByID(store: store, id: eventID) else {
            throw CalendarBackendError.message("Event not found: \(eventID)")
        }
        let payload = [
            "deleted": true,
            "calendar": event.calendar.title,
            "uid": event.eventIdentifier as Any,
            "id": event.eventIdentifier as Any,
            "summary": event.title ?? ""
        ] as [String : Any]
        let span: EKSpan = (event.recurrenceRules?.isEmpty == false) ? .futureEvents : .thisEvent
        try store.remove(event, span: span, commit: true)
        try emitJSON(payload)

    default:
        throw CalendarBackendError.message("Unsupported backend command: \(command)")
    }
} catch {
    let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    fputs("\(message)\n", stderr)
    exit(1)
}
