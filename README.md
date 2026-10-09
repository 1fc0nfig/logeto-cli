# Logeto CLI

An unofficial terminal client for Logeto (Výkaz práce) timesheets. It lists, adds, and deletes timesheet records. It is one Python file and uses only the standard library.

> **Unofficial.** Logeto CLI is not made, supported, or endorsed by Systemart s.r.o. "Logeto" and "Výkaz práce" are their product names. The tool sends the same requests as the Logeto web page. A change to that page can stop the tool at any time. Use it only on your own account, and follow the rules of your company. The tool cannot do more than your account can do in the browser.

If your company has a Logeto API key, use the [official API](https://documentation.logeto.com/api/logeto-public-api/) instead. This tool is for users without a key.

## Quick start with an AI assistant

Open [LLM_QUICKSTART.md](LLM_QUICKSTART.md) and paste its prompt into your AI assistant. The assistant installs the tool, asks for your Logeto address, reads the form setup of your company, and sets your defaults. You type your password only into your own terminal.

## Manual setup

```
pipx install git+<REPO_URL>
logeto login --url https://mycompany.vykazprace.cz --email me@mycompany.com
logeto init            # shows your company's form setup and suggests defaults
logeto init --apply    # saves the suggested contract and activity
```

`logeto login` asks for the password. On macOS the password goes to the Keychain under the service `logeto-cli`. When the session expires, the tool then logs in again by itself. On other systems, set `LOGETO_PASSWORD` for the automatic login, or run `logeto login` again.

`logeto logout` ends the session on the server and deletes the local cookies. Add `--forget` to also remove the stored password.

If your company uses single sign-on, log in in the browser. In the developer tools, copy one timesheet request as cURL. Then run `logeto cookie --url <address> '<the curl command>'`.

## Use

```
logeto ls                      # this month
logeto ls last                 # last month
logeto ls 2026-09 --json       # for scripts
logeto add today 9:00-17:30 "Planning and reviews"
logeto add today 7:30 "Vacation" -a dovolená      # total hours only
logeto add 12.10. 9-12 "Workshop" -c 170 -n      # dry run with another contract
logeto rm 4321                 # the key is the first column of `logeto ls`
logeto contracts               # ids and names
logeto activities              # [hours] or [span] shows the time entry rule
logeto config contract "Development"
```

`add` and `rm` show the record and ask before they write. Use `-y` to skip the question. `-c` and `-a` accept an id, a full name, or a unique part of a name.

An activity can require a time span (`9:00-17:30`) or only the total (`7:30` or `7.5h`). `logeto activities` shows the rule, and `add` checks it before it sends the record.

The config is in `~/.config/logeto/config.json`. The session cookies are in `~/.config/logeto/cookies.lwp` (mode 600). Set `LOGETO_HOME` to use another directory.

## What `logeto init` reads

Each company can change the timesheet form. `logeto init` reads the settings that the page sends to the browser:

- the optional fields that the company turned on: contract, subcontract, identifiers 1 to 8
- the required fields
- whether manual records are allowed
- the time entry rule of each activity
- the contracts and activities that your account can use

It also reads your records of the last three months and suggests the contract and activity that you use most. `--json` gives the full result for scripts and AI assistants.

## Limits

- The server can block records for past dates. The tool shows the message of the server.
- The tool cannot edit a record. Delete it and add it again.
- The tool cannot fill subcontracts, identifiers, locations, or custom rates. If your company requires one of these fields, `logeto init` shows a warning, and `add` fails.
- `rm` works only in a month with 20 records or fewer, because the grid shows 20 rows per page.
- If a new record overlaps an existing one, the server returns an error. The tool shows the error and stops.
- `ls` sets the month filter of the timesheet page on the server. The browser shows the same month on the next load.
- The tool was tested on one company with the Czech interface. Other languages use the same requests. Report a problem with the output of `logeto init --json`, without your e-mail.

## Protocol notes

The timesheet page (`/wf/<lang>/Employees/Timesheet`) is ASP.NET WebForms with DevExpress 18.1. The tool sends the same requests as the browser.

- **Login.** GET `/Login`, then POST the form `loginForm` with `AccountName`, `Email`, `Password`, `RememberMe`, and `__RequestVerificationToken`. The page script `ServerConstants` gives the language, the account name, and the date format.
- **Company settings.** The hidden field `webEntities` (`ctl00_DC_HiddenFieldEntities`) holds `AG_*` (fields that are on), `RF_*` (required fields), `ACTIVITY_TYPES` (with `TIME_ENTRY`: 0 any, 1 total only, 2 span only), and `SC_TIME_TRACKING_MANUAL_RECORDS_ENABLED`.
- **Month filter.** A postback with `__EVENTTARGET=ctl00$DC$GridWebPart1$FilterPanel$DateRange`. The filter is an `ASPxHiddenField`. Its state field holds `{"data": ...}` in the DevExpress HiddenFieldSerializer format, for example `12|#|RangeType|18|1|7Month|2|1|9MonthYear|2|4|2026#`. RangeType 7 is one month.
- **Grid callback.** POST all page inputs plus `__CALLBACKID=ctl00$DC$GridWebPart1$GridWebPart` and `__CALLBACKPARAM=c0:KV|<len>;<keys json>;GB|<len>;<args>;`. Each argument is written as `<len>|<value>`. The grid state goes in the field `ctl00$DC$GridWebPart1$GridWebPart` as HTML-escaped JSON. The response is `<len>|<__EVENTVALIDATION>/*DX*/({result})`. A server error is in `result.error.message`.
- **Add.** The callback `ADDNEWROW` returns the popup edit form. The callback `UPDATEEDIT` posts the filled form. Combo boxes post the value in `<ClientID>_VI` and the text in the input. Date and time editors also post `<uniqueID>$State = {"rawValue": "<ms>"}`. The value is the wall-clock time read as UTC. Time editors use 1 January of the record year as the date.
- **Delete.** A postback with `__EVENTTARGET=ctl00$DC$GridWebPart1$ToolBar` and `__EVENTARGUMENT=CLICK:2`. The grid state marks the row in `focusedRow` (row index) and `selection` (`T` or `F` for each row on the page). The tool checks the key list after the delete.
- **Paging.** The callback `PAGERONCLICK` with the argument `PN<page index>`.
- **Grid columns.** `cpVisibleColumns` and `columnProp` map the table cells to field names such as `DATUM`, `_CAS_OD`, and `POPIS`, so the column order of a user does not matter.
