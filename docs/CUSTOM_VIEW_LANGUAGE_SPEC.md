# Custom View Language Specification (Draft)

This document proposes a small, purpose-built language for creating **custom views** over the app’s data (lists, todos, datetime dividers, hashtags, and calendar events). The goal is to let a power user define views like “Uncompleted, but grouped by list, excluding collation collected items, sorted by pinned then modified” without writing application code.

The spec is **web framework agnostic** and describes **behavior** and **data contracts**, not how it is implemented.

---

## 1. Goals & non-goals

### Goals

- Let users define read-only “views” with:
  - inclusion/exclusion rules (filters)
  - sorting
  - grouping
  - projections (which fields to show)
  - computed columns
  - lightweight joins/expansions (e.g., list → todos, todo → children)
  - optional pagination/limits
- Provide stable semantics across UI pages.
- Remain safe: no arbitrary file/network access.
- Be easy to author and store as plain text.

### Non-goals

- Replacing the app’s full UI.
- Arbitrary mutation of data (creating, editing, deleting) from the language.
  - (Optional future extension: “actions” as pre-defined safe operations.)
- Being a general-purpose programming language.

---

## 2. Name

**STVL** — *Simple Todo View Language*.

---

## 3. Data model exposed to STVL

STVL operates on a set of **collections** and **records**.

### 3.1 Collections

- `lists`
- `todos`
- `datetime_dividers` (a.k.a. datetime headers)
- `hashtags`
- `named_searches`
- `calendar_events`
- `calendar_occurrences` (derived from `calendar_events` + recurrence rules + completion records)

### 3.2 Common record fields

All records have at least:

- `id: integer`

### 3.3 Lists

- `id`
- `name: string`
- `completed: boolean`
- `archived: boolean`
- `pinned: boolean`
- `pinned_at: datetime?`
- `created_at: datetime`
- `modified_at: datetime`
- `show_completed: boolean`
- `is_collation: boolean`
- `collation_color: string?` (e.g., `"#aabbcc"`)
- `hashtags: [hashtag]` (list of hashtag records)

Derived/virtual:

- `uncompleted_todo_count: integer` (count of todos where `completed == false`, optionally controlled by query)

### 3.4 Todos

- `id`
- `list_id: integer`
- `title: string`
- `note: string?`
- `has_note: boolean`
- `completed: boolean`
- `pinned: boolean`
- `pinned_at: datetime?`
- `created_at: datetime`
- `modified_at: datetime`
- `parent_id: integer?`
- `hashtags: [hashtag]`

Expansions:

- `list: list` (parent list)
- `children: [todo]` (subtodos)
- `collation_lists: [list]` (lists that collect this todo)

### 3.5 Datetime dividers

- `id`
- `list_id: integer`
- `created_at: datetime`

### 3.6 Hashtags

- `id`
- `name: string`
- `show_completed: boolean`
- `created_at: datetime`

### 3.7 Calendar events

- `id`
- `title: string`
- `note: string?`
- `start_at: datetime`
- `rrule: string?` (recurrence rule)
- `created_at: datetime`
- `modified_at: datetime`

### 3.8 Calendar occurrences (derived)

A `calendar_occurrences` record represents a date occurrence of an event.

- `event_id: integer`
- `occurrence_at: date` (or datetime normalized to date)
- `completed: boolean`
- `event: calendar_event` (embedded)

---

## 4. Time & formatting rules

- All stored timestamps are treated as UTC for storage semantics.
- All displayed timestamps are converted to the **server’s local time** for formatting.
- Default date format: `D/M/YYYY`.
- Default datetime format: `D/M/YYYY HH:MM`.

STVL supports explicit formatting via `format()` (see functions).

---

## 5. Execution model

A STVL script evaluates to one **ViewResult**:

- `title: string`
- `sections: [Section]`

Each `Section` has:

- `title: string?`
- `rows: [Row]`
- optional `layout` hints (e.g., table vs list)

STVL is evaluated in a **sandbox**:

- no file system reads/writes
- no network access
- bounded runtime and memory
- bounded record expansion depth (e.g., subtodo recursion)

---

## 6. Language overview

STVL is a line-oriented DSL with blocks.

### 6.1 Minimal example

```stvl
VIEW "Uncompleted"
FROM lists
WHERE archived == false AND completed == false
EXPAND todos WHERE completed == false
ORDER BY modified_at DESC
RENDER list_grouped
```

---

## 7. Syntax (EBNF-like)

Whitespace is flexible. Strings use double quotes.

```
script          := view_decl { statement }

view_decl       := "VIEW" string

statement       := from_stmt
                 | where_stmt
                 | expand_stmt
                 | join_stmt
                 | let_stmt
                 | order_stmt
                 | group_stmt
                 | select_stmt
                 | limit_stmt
                 | page_stmt
                 | render_stmt
                 | section_stmt

from_stmt       := "FROM" ident

where_stmt      := "WHERE" expr

expand_stmt     := "EXPAND" ident ["AS" ident] ["WHERE" expr] [order_stmt] [limit_stmt]

join_stmt       := "JOIN" ident "ON" expr ["AS" ident]

let_stmt        := "LET" ident "=" expr

order_stmt      := "ORDER" "BY" order_item { "," order_item }
order_item      := ident ["ASC"|"DESC"]

group_stmt      := "GROUP" "BY" expr ["AS" ident]

select_stmt     := "SELECT" select_item { "," select_item }
select_item     := expr ["AS" ident]

limit_stmt      := "LIMIT" integer
page_stmt       := "PAGE" integer "SIZE" integer

render_stmt     := "RENDER" ident

section_stmt    := "SECTION" string "{" { statement } "}"

expr            := ... (see Expression grammar)
```

### 7.1 Expressions

Expressions support:

- literals: strings, integers, booleans, null
- record field access: `field`, or `alias.field`
- operators:
  - boolean: `AND`, `OR`, `NOT`
  - comparisons: `==`, `!=`, `<`, `<=`, `>`, `>=`
  - membership: `IN`, `CONTAINS`
- parentheses

Examples:

- `archived == false`
- `hashtags CONTAINS "work"`
- `title CONTAINS "report" OR note CONTAINS "report"`

---

## 8. Core statements semantics

### 8.1 FROM

Selects the root collection for the view.

- `FROM lists` means rows are list records.
- `FROM todos` means rows are todo records.

### 8.2 WHERE

Filters root records. Only records where the expression evaluates truthy remain.

### 8.3 EXPAND

Attaches a child collection to each root record.

Examples:

- `EXPAND todos WHERE completed == false ORDER BY modified_at DESC`
- `EXPAND children WHERE completed == false`

**Important:** expansions do not change the root row set; they add nested rows.

### 8.4 ORDER BY

Sorts the root record list.

### 8.5 GROUP BY

Groups root records into sections.

Example:

- `GROUP BY first(hashtag_names) AS tag`

### 8.6 SELECT

Controls which fields are rendered for each row.

Example:

- `SELECT name, uncompleted_todo_count AS open`

### 8.7 LIMIT / PAGE

Limits the number of root records.

- `LIMIT 50`
- `PAGE 2 SIZE 25` (offset-based paging)

### 8.8 RENDER

Chooses a rendering style understood by the UI.

Recommended built-ins:

- `RENDER table`
- `RENDER list_grouped` (e.g., lists as headings with nested todos)
- `RENDER timeline` (calendar-style)

If a renderer is unknown, the UI may fall back to a generic table.

---

## 9. Functions (standard library)

Functions are pure and side-effect free.

### 9.1 Text

- `lower(s)`
- `upper(s)`
- `contains(haystack, needle)`
- `startswith(s, prefix)`

### 9.2 Dates/times

- `now()` → current timestamp
- `today()` → current date
- `days(n)` → duration
- `format(dt, "D/M/YYYY")` or `format(dt, "D/M/YYYY HH:MM")`

### 9.3 Aggregates

When grouping:

- `count()`
- `min(expr)` / `max(expr)`
- `sum(expr)`

### 9.4 Helpers

- `coalesce(a, b)`
- `if(cond, a, b)`

---

## 10. Collation-specific behavior

STVL should expose a consistent way to include/exclude collation “collected” todos.

Two recommended approaches:

### 10.1 Treat “collected todos” as a separate expansion

- `EXPAND collected_todos WHERE completed == false`

Only valid when the root record is a list with `is_collation == true`.

### 10.2 A global option

Allow scripts to set options:

```stvl
OPTION include_collation_collected = true
```

(Options are not mandatory for v1; expansions alone may be sufficient.)

---

## 11. Error handling

- Unknown collection name → validation error.
- Unknown field name → validation error.
- Type mismatch in expression → validation error.
- Expansion depth exceeded → runtime error with partial results allowed.

Errors should include:

- line number
- token/field name
- human-readable message

---

## 12. Security & performance constraints

Implementations SHOULD enforce:

- max records scanned per collection
- max result rows
- max expansion depth (e.g., subtodo recursion)
- max execution time

No access to:

- file system
- environment variables
- shell commands
- network

---

## 13. Examples

### 13.1 “Uncompleted” (similar to current Uncompleted page)

```stvl
VIEW "Uncompleted"
FROM lists
WHERE archived == false
LET open = uncompleted_todo_count
WHERE open > 0
ORDER BY modified_at DESC
EXPAND todos WHERE completed == false AND parent_id == null ORDER BY modified_at DESC
EXPAND children WHERE completed == false ORDER BY created_at DESC
RENDER list_grouped
```

### 13.2 Uncompleted, but exclude collation collected items

```stvl
VIEW "Uncompleted (No Collations)"
FROM lists
WHERE archived == false AND is_collation == false
LET open = uncompleted_todo_count
WHERE open > 0
ORDER BY modified_at DESC
EXPAND todos WHERE completed == false AND parent_id == null
RENDER list_grouped
```

### 13.3 Collation dashboard: show collation lists and their collected open todos

```stvl
VIEW "Collations — Open Collected"
FROM lists
WHERE archived == false AND is_collation == true
ORDER BY name ASC
EXPAND collected_todos WHERE completed == false AND parent_id == null ORDER BY modified_at DESC
RENDER list_grouped
```

### 13.4 Calendar: occurrences in next 14 days

```stvl
VIEW "Calendar (Next 14 Days)"
FROM calendar_occurrences
WHERE occurrence_at >= today() AND occurrence_at <= today() + days(14)
ORDER BY occurrence_at ASC
SELECT occurrence_at, event.title AS title, completed
RENDER timeline
```

---

## 14. Storage & UI integration (suggested)

- Scripts are stored as plain text.
- A “Views” page may list saved scripts.
- Running a view produces a read-only page with:
  - sections
  - selection checkboxes optional (implementation choice)
  - links to underlying list/todo/calendar pages where appropriate

---

*End of draft specification.*
