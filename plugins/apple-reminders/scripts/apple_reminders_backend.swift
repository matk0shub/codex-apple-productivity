import Foundation
import EventKit

enum BackendError: Error, LocalizedError {
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
    throw BackendError.message("Invalid ISO datetime: \(value)")
}

func requireArg(_ name: String, in args: [String]) throws -> String {
    guard let idx = args.firstIndex(of: name), idx + 1 < args.count else {
        throw BackendError.message("Missing required argument \(name)")
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
        throw BackendError.message("Expected JSON array for \(name)")
    }
    return decoded
}

func requestRemindersAccess(store: EKEventStore) throws {
    let sem = DispatchSemaphore(value: 0)
    var granted = false
    var accessError: Error?

    if #available(macOS 14.0, *) {
        store.requestFullAccessToReminders { ok, err in
            granted = ok
            accessError = err
            sem.signal()
        }
    } else {
        store.requestAccess(to: .reminder) { ok, err in
            granted = ok
            accessError = err
            sem.signal()
        }
    }

    _ = sem.wait(timeout: .now() + 30)
    if let accessError {
        throw accessError
    }
    if !granted {
        throw BackendError.message("Reminders access was not granted.")
    }
}

func fetchReminders(store: EKEventStore, predicate: NSPredicate) throws -> [EKReminder] {
    let sem = DispatchSemaphore(value: 0)
    var fetched: [EKReminder] = []
    store.fetchReminders(matching: predicate) { reminders in
        fetched = reminders ?? []
        sem.signal()
    }
    _ = sem.wait(timeout: .now() + 30)
    return fetched
}

func selectedCalendars(store: EKEventStore, names: [String]) -> [EKCalendar] {
    let calendars = store.calendars(for: .reminder)
    return calendars.filter { names.contains($0.title) }
}

func firstAlarmDate(for reminder: EKReminder) -> Date? {
    reminder.alarms?.compactMap { $0.absoluteDate }.sorted().first
}

func dueDate(for reminder: EKReminder) -> Date? {
    guard let comps = reminder.dueDateComponents else { return nil }
    return Calendar.current.date(from: comps)
}

func reminderRecord(_ reminder: EKReminder) -> [String: Any] {
    [
        "list": reminder.calendar.title,
        "id": reminder.calendarItemIdentifier,
        "externalId": reminder.calendarItemExternalIdentifier,
        "name": reminder.title,
        "body": reminder.notes as Any,
        "completed": reminder.isCompleted,
        "creationDate": isoString(reminder.creationDate) as Any,
        "modificationDate": isoString(reminder.lastModifiedDate) as Any,
        "completionDate": isoString(reminder.completionDate) as Any,
        "dueDate": isoString(dueDate(for: reminder)) as Any,
        "remindMeDate": isoString(firstAlarmDate(for: reminder)) as Any,
        "priority": reminder.priority,
        "flagged": false,
        "recurrence": recurrenceRecord(reminder) as Any
    ]
}

func emitJSON(_ payload: Any) throws {
    let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted])
    guard let text = String(data: data, encoding: .utf8) else {
        throw BackendError.message("Failed to encode JSON output.")
    }
    print(text)
}

func toDateComponents(_ date: Date) -> DateComponents {
    Calendar.current.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
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

func recurrenceRecord(_ reminder: EKReminder) -> [String: Any]? {
    guard let rule = reminder.recurrenceRules?.first else { return nil }
    var payload: [String: Any] = ["interval": rule.interval]
    switch rule.frequency {
    case .daily: payload["frequency"] = "daily"
    case .weekly: payload["frequency"] = "weekly"
    case .monthly: payload["frequency"] = "monthly"
    case .yearly: payload["frequency"] = "yearly"
    @unknown default: payload["frequency"] = "daily"
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
        return EKRecurrenceRule(recurrenceWith: .daily, interval: interval, end: nil)
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
        throw BackendError.message("Unsupported repeat value: \(repeatValue)")
    }
}

let store = EKEventStore()

do {
    try requestRemindersAccess(store: store)
    let args = Array(CommandLine.arguments.dropFirst())
    guard let command = args.first else {
        throw BackendError.message("Missing command.")
    }

    switch command {
    case "list-lists":
        try emitJSON(store.calendars(for: .reminder).map { $0.title })

    case "list":
        let listNames = try jsonArrayArg("--lists-json", in: args)
        let includeCompleted = boolArg("--include-completed", in: args)
        let limit = intArg("--limit", in: args, default: 200)
        let calendars = selectedCalendars(store: store, names: listNames)
        let predicate = store.predicateForReminders(in: calendars)
        let reminders = try fetchReminders(store: store, predicate: predicate)
            .filter { includeCompleted || !$0.isCompleted }
            .prefix(limit)
            .map(reminderRecord)
        try emitJSON(reminders)

    case "query-day":
        let listNames = try jsonArrayArg("--lists-json", in: args)
        let day = try requireArg("--date", in: args)
        guard let dayDate = DateFormatter.iso8601Full.date(from: "\(day)T00:00:00") else {
            throw BackendError.message("Invalid day value \(day)")
        }
        let nextDay = Calendar.current.date(byAdding: .day, value: 1, to: dayDate)!
        let calendars = selectedCalendars(store: store, names: listNames)
        let predicate = store.predicateForIncompleteReminders(
            withDueDateStarting: dayDate,
            ending: nextDay,
            calendars: calendars
        )
        let reminders = try fetchReminders(store: store, predicate: predicate).map(reminderRecord)
        try emitJSON(reminders)

    case "query-overdue":
        let listNames = try jsonArrayArg("--lists-json", in: args)
        let beforeRaw = try requireArg("--before", in: args)
        let beforeDate = try parseISODate(beforeRaw)
        let calendars = selectedCalendars(store: store, names: listNames)
        let predicate = store.predicateForIncompleteReminders(
            withDueDateStarting: nil,
            ending: beforeDate,
            calendars: calendars
        )
        let reminders = try fetchReminders(store: store, predicate: predicate).map(reminderRecord)
        try emitJSON(reminders)

    case "query-alarm-day":
        let listNames = try jsonArrayArg("--lists-json", in: args)
        let day = try requireArg("--date", in: args)
        guard let dayDate = DateFormatter.iso8601Full.date(from: "\(day)T00:00:00") else {
            throw BackendError.message("Invalid day value \(day)")
        }
        let nextDay = Calendar.current.date(byAdding: .day, value: 1, to: dayDate)!
        let calendars = selectedCalendars(store: store, names: listNames)
        let predicate = store.predicateForReminders(in: calendars)
        let reminders = try fetchReminders(store: store, predicate: predicate)
            .filter { !$0.isCompleted }
            .filter { reminder in
                guard let alarm = firstAlarmDate(for: reminder) else { return false }
                return alarm >= dayDate && alarm < nextDay
            }
            .map(reminderRecord)
        try emitJSON(reminders)

    case "find":
        let listNames = try jsonArrayArg("--lists-json", in: args)
        let includeCompleted = boolArg("--include-completed", in: args)
        let reminderID = optionalArg("--id", in: args)
        let title = optionalArg("--title", in: args)
        let calendars = selectedCalendars(store: store, names: listNames)
        let predicate = includeCompleted
            ? store.predicateForReminders(in: calendars)
            : store.predicateForIncompleteReminders(withDueDateStarting: nil, ending: nil, calendars: calendars)
        let reminders = try fetchReminders(store: store, predicate: predicate).filter { reminder in
            if let reminderID, reminder.calendarItemIdentifier != reminderID { return false }
            if let title, reminder.title.trimmingCharacters(in: .whitespacesAndNewlines).caseInsensitiveCompare(title.trimmingCharacters(in: .whitespacesAndNewlines)) != .orderedSame {
                return false
            }
            return true
        }.map(reminderRecord)
        try emitJSON(reminders)

    case "add":
        let listName = try requireArg("--list", in: args)
        let title = try requireArg("--title", in: args)
        let priority = intArg("--priority", in: args, default: 0)
        let body = optionalArg("--body", in: args)
        let dueISO = optionalArg("--due-iso", in: args)
        let remindISO = optionalArg("--remind-iso", in: args)
        guard let calendar = store.calendars(for: .reminder).first(where: { $0.title == listName }) else {
            throw BackendError.message("List not found: \(listName)")
        }
        let reminder = EKReminder(eventStore: store)
        reminder.calendar = calendar
        reminder.title = title
        reminder.priority = priority
        reminder.notes = body
        if let dueISO {
            reminder.dueDateComponents = toDateComponents(try parseISODate(dueISO))
        }
        if let remindISO {
            reminder.alarms = [EKAlarm(absoluteDate: try parseISODate(remindISO))]
        }
        if let rule = try recurrenceRule(from: args) {
            reminder.recurrenceRules = [rule]
        }
        try store.save(reminder, commit: true)
        var payload = reminderRecord(reminder)
        payload["created"] = true
        try emitJSON(payload)

    case "update":
        let reminderID = try requireArg("--id", in: args)
        let listName = optionalArg("--list", in: args)
        let title = optionalArg("--title", in: args)
        let body = optionalArg("--body", in: args)
        let priorityRaw = optionalArg("--priority", in: args)
        let dueISO = optionalArg("--due-iso", in: args)
        let remindISO = optionalArg("--remind-iso", in: args)
        let clearDue = boolArg("--clear-due", in: args)
        let clearRemind = boolArg("--clear-remind", in: args)
        let clearRepeat = boolArg("--clear-repeat", in: args)
        let moveToList = optionalArg("--move-to-list", in: args)
        let calendars = listName != nil
            ? selectedCalendars(store: store, names: [listName!])
            : store.calendars(for: .reminder)
        let predicate = store.predicateForReminders(in: calendars)
        let reminders = try fetchReminders(store: store, predicate: predicate)
        guard let reminder = reminders.first(where: { $0.calendarItemIdentifier == reminderID }) else {
            throw BackendError.message("Reminder not found: \(reminderID)")
        }
        if let moveToList {
            guard let targetCalendar = store.calendars(for: .reminder).first(where: { $0.title == moveToList }) else {
                throw BackendError.message("Target list not found: \(moveToList)")
            }
            reminder.calendar = targetCalendar
        }
        if let title { reminder.title = title }
        if let body { reminder.notes = body }
        if let priorityRaw, let priority = Int(priorityRaw) { reminder.priority = priority }
        if clearDue {
            reminder.dueDateComponents = nil
        } else if let dueISO {
            reminder.dueDateComponents = toDateComponents(try parseISODate(dueISO))
        }
        if clearRemind {
            reminder.alarms = nil
        } else if let remindISO {
            reminder.alarms = [EKAlarm(absoluteDate: try parseISODate(remindISO))]
        }
        if clearRepeat {
            reminder.recurrenceRules = nil
        } else if let rule = try recurrenceRule(from: args) {
            reminder.recurrenceRules = [rule]
        }
        try store.save(reminder, commit: true)
        try emitJSON(reminderRecord(reminder))

    case "complete":
        let listName = try requireArg("--list", in: args)
        let reminderID = try requireArg("--id", in: args)
        guard let calendar = store.calendars(for: .reminder).first(where: { $0.title == listName }) else {
            throw BackendError.message("List not found: \(listName)")
        }
        let predicate = store.predicateForReminders(in: [calendar])
        let reminders = try fetchReminders(store: store, predicate: predicate)
        guard let reminder = reminders.first(where: { $0.calendarItemIdentifier == reminderID }) else {
            throw BackendError.message("Reminder not found: \(reminderID)")
        }
        reminder.isCompleted = true
        reminder.completionDate = Date()
        try store.save(reminder, commit: true)
        try emitJSON([
            "completed": true,
            "list": listName,
            "id": reminder.calendarItemIdentifier,
            "name": reminder.title,
            "completionDate": isoString(reminder.completionDate) as Any
        ])

    case "reopen":
        let listName = optionalArg("--list", in: args)
        let reminderID = try requireArg("--id", in: args)
        let calendars = listName != nil
            ? selectedCalendars(store: store, names: [listName!])
            : store.calendars(for: .reminder)
        let predicate = store.predicateForReminders(in: calendars)
        let reminders = try fetchReminders(store: store, predicate: predicate)
        guard let reminder = reminders.first(where: { $0.calendarItemIdentifier == reminderID }) else {
            throw BackendError.message("Reminder not found: \(reminderID)")
        }
        reminder.isCompleted = false
        reminder.completionDate = nil
        try store.save(reminder, commit: true)
        try emitJSON([
            "completed": false,
            "list": reminder.calendar.title,
            "id": reminder.calendarItemIdentifier,
            "name": reminder.title,
            "completionDate": reminder.completionDate as Any
        ])

    case "delete":
        let listName = optionalArg("--list", in: args)
        let reminderID = try requireArg("--id", in: args)
        let calendars = listName != nil
            ? selectedCalendars(store: store, names: [listName!])
            : store.calendars(for: .reminder)
        let predicate = store.predicateForReminders(in: calendars)
        let reminders = try fetchReminders(store: store, predicate: predicate)
        guard let reminder = reminders.first(where: { $0.calendarItemIdentifier == reminderID }) else {
            throw BackendError.message("Reminder not found: \(reminderID)")
        }
        let payload = [
            "deleted": true,
            "list": reminder.calendar.title,
            "id": reminder.calendarItemIdentifier,
            "name": reminder.title
        ] as [String : Any]
        try store.remove(reminder, commit: true)
        try emitJSON(payload)

    default:
        throw BackendError.message("Unsupported backend command: \(command)")
    }
} catch {
    let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    fputs("\(message)\n", stderr)
    exit(1)
}

extension DateFormatter {
    static let iso8601Full: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone.current
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return formatter
    }()
}
