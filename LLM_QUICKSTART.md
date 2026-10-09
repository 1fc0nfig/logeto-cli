# Logeto CLI: quick start for your AI assistant

Copy everything inside the box below into your AI assistant. An assistant that can run terminal commands works best, for example Claude Code, Codex, or Cursor. A chat-only assistant also works: it gives you the commands, and you paste the output back.

````text
You are helping me set up Logeto CLI, an UNOFFICIAL terminal client for my company's Logeto
(Výkaz práce) timesheet. Talk to me in my language. Follow these steps in order. Do one step at
a time and wait for me when a step needs my answer.

Rules for the whole session:
- Never ask me for my password in this chat, and never type it into a command yourself.
  I enter it only at the password prompt of `logeto login`, in my own terminal.
- Never save or delete timesheet records without my clear "yes" for that record.
- If a command fails, show me the error and explain it. Do not guess at fixes in my account.

1. Tell me, in two sentences, that Logeto CLI is unofficial and is not made or supported by
   Systemart s.r.o. (the maker of Logeto). It uses the same requests as the web page. My company
   or the vendor can change the web page at any time, and then the tool can stop working. Ask me
   if I want to continue.

2. Check that Python 3.10 or newer is installed (`python3 --version`). Install the tool:
   `pipx install git+<REPO_URL>`. If pipx is missing, use `python3 -m pip install --user git+<REPO_URL>`.
   Check the install with `logeto --version`.

3. Ask me for the web address where I log in to Logeto, for example
   https://mycompany.vykazprace.cz or https://mycompany.logeto.com. Ask me for my login e-mail.

4. Tell me to run this command myself in my terminal, and to type my password at the prompt:
   `logeto login --url <ADDRESS> --email <EMAIL>`
   On macOS the password goes to the Keychain. On other systems the tool keeps only the session
   cookies. If the login page asks for a company account name, add `--account <NAME>`.
   If the login fails because my company uses single sign-on, tell me to log in in the browser,
   open the developer tools, copy one timesheet request as cURL, and run
   `logeto cookie --url <ADDRESS> '<the curl command>'`.

5. Run `logeto init --json` and read the result:
   - `manual_records_allowed`: if false, tell me that my company does not allow manual records,
     so `logeto add` will not work. Listing still works.
   - `unsupported_required`: if not empty, tell me which required fields the tool cannot fill
     yet. Saving records will fail until that changes.
   - `fields`: tell me which fields my company uses and which are required.
   - `activities`: each has `time`. "span" means a start and an end time, "hours" means only the
     total, "any" means both are allowed.
   - `history` and `suggested_defaults`: the contract and activity I use most.

6. Show me the suggested default contract and activity. Ask me to confirm or change them. Then
   set them with `logeto config contract "<name or id>"` and `logeto config activity "<name>"`.
   Use `logeto contracts` and `logeto activities` to look up names.

7. Show me a dry run for today: `logeto add today 9:00-17:00 "Test" -n`. Explain the output.
   It does not save anything.

8. Give me this cheat sheet, filled in with my defaults:
   - `logeto ls` (this month), `logeto ls last`, `logeto ls 2026-09 --json`
   - `logeto add today 9:00-17:30 "what I did"`, or the total only: `logeto add today 7:30 "..."`
   - `-c <contract>` and `-a <activity>` change the defaults for one record
   - `logeto rm <key>` (the key is the first column of `logeto ls`)
   - `logeto init` again if my company changes the form

9. Tell me the limits: the server can block entries for past dates, the tool cannot edit a
   record (delete it and add it again), and it cannot fill subcontracts, identifiers, locations,
   or custom rates yet.
````

The maintainer replaces `<REPO_URL>` with the address of the published repository.
