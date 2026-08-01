# 4. Talkgroup names

A **talkgroup** is a channel on the radio system — "Grand Junction Fire
dispatch" is one, "Mesa County Sheriff patrol" is another. On the air they are
just numbers, so the system needs a lookup table to show names.

This project ships with four, taken from an observed capture of the Grand
Junction site:

| Number | Name |
| --- | --- |
| 8020 | CDOT 2K |
| 8023 | CDOT 2T |
| 8424 | (unidentified) |
| 8441 | GJ Fire Disp |

Everything else still gets recorded and transcribed — it just shows up as a bare
number like `TG 8102`. Filling in the rest is what this page is about.

---

## Where the full list comes from

**[RadioReference.com](https://www.radioreference.com/)** maintains the
database. The Grand Junction site belongs to the **State of Colorado DTRS**,
system ID **329**:

> https://www.radioreference.com/db/sid/329

Downloading the list as a file requires a **Premium Subscription** (about \$30 a
year). It is worth it if you are going to run this seriously — the database is
also how you find out what everything is.

---

## Option A: download it (Premium subscription)

1. Log in at [radioreference.com](https://www.radioreference.com/).
2. Go to <https://www.radioreference.com/db/sid/329>.
3. Open the **Talkgroups** tab.
4. Colorado DTRS is a statewide system with thousands of talkgroups, so filter
   it down. Use the county filter for **Mesa** — or grab the categories you care
   about (Grand Junction, Mesa County, CDOT Region 3, Fire, Law, EMS).
5. Click the **export / download** link to save it as a CSV file.
6. Copy that file onto the server, replacing your working copy:

   ```bash
   cp ~/Downloads/your-download.csv config/trunk-recorder/talkgroups.csv
   docker compose restart trunk-recorder
   ```

   `talkgroups.csv` is git-ignored, so the list you build up here is yours and
   survives every `git pull`. Only `talkgroups.csv.example` — the four-entry
   starter — is tracked.

RadioReference's export format is already what Trunk Recorder expects, with one
exception — see [the Priority column](#the-priority-column) below.

---

## Option B: copy it by hand (free)

Without a subscription you can still read the tables on the site.

1. Go to <https://www.radioreference.com/db/sid/329>, open the **Talkgroups**
   tab, and find the Mesa County / Grand Junction categories.
2. Click **"List all in one table"**.
3. Select the table in your browser, copy it, and paste into a spreadsheet
   (LibreOffice Calc, Excel, Google Sheets).
4. Arrange the columns to match the format below.
5. Save as CSV and copy it to `config/trunk-recorder/talkgroups.csv`.

Tedious, but it is a one-time job, and you can do it a few categories at a time.

---

## Option C: let it teach you

You do not have to do anything at all. Run the system for a week and watch the
transcript page. Dispatchers identify themselves constantly, so it becomes
obvious what most talkgroups are:

> *"Grand Junction, Engine 4 is on scene..."*

Then add lines for the ones you have worked out, as you work them out.

---

## The file format

`config/trunk-recorder/talkgroups.csv` looks like this:

```csv
Decimal,Hex,Mode,Alpha Tag,Description,Tag,Category,Priority
8441,20F9,D,GJ Fire Disp,Grand Junction Fire dispatch,Fire Dispatch,Fire,1
```

| Column | Required | What it is |
| --- | :---: | --- |
| `Decimal` | yes | The talkgroup number. **Must be the first column.** |
| `Hex` | | Same number in hexadecimal. Not used — leave it or fill it in. |
| `Mode` | yes | `D` for digital, `A` for analog, `T` for TDMA. On this system, use `D`. Add `E` for fully encrypted (`DE`). |
| `Alpha Tag` | | Short name, 16 characters or so. **This is what shows up everywhere**, so make it count. |
| `Description` | | Longer description. |
| `Tag` | | The service type — `Fire Dispatch`, `Law Dispatch`, `EMS-Talk`. |
| `Category` | | The grouping — `Fire`, `Law`, `EMS`, `CDOT`. This drives the category filter on the transcript page and the colour of Discord posts. |
| `Priority` | | See below. |

A header row on the first line is required.

### The Priority column

RadioReference does not include this, so if you downloaded their CSV you may
want to add it.

It means: *how many free recorders must there be before this call is worth
recording?* Lower numbers win.

- `1` — always record. Use for dispatch channels.
- `2`, `3` — record unless things are busy.
- `-1` — **never record this talkgroup.**

If the column is missing, everything is treated as priority 1. With
`digitalRecorders` set to 6 this rarely matters, but it is how you keep a busy
channel from crowding out a dispatch channel.

`-1` is also the clean way to ignore a talkgroup entirely — a chatty data
channel, or something you would rather not record.

### Encrypted talkgroups

Mark them with a trailing `E` on the mode:

```csv
8500,2134,DE,MCSO Tac 1,Mesa County Sheriff tactical,Law Tac,Law,-1
```

The system detects encryption on its own and skips those calls, so this is
about keeping your log tidy rather than making anything work.

---

## Checking your file

CSV files break in quiet ways — a stray comma inside a description will silently
shift every column after it. After editing, check it:

```bash
python3 -c "
import csv
rows = list(csv.DictReader(open('config/trunk-recorder/talkgroups.csv')))
print(f'{len(rows)} talkgroups loaded')
print('columns:', list(rows[0].keys()))
bad = [r for r in rows if not (r['Decimal'] or '').strip().isdigit()]
print('bad rows:', bad[:3] if bad else 'none')
"
```

If a description contains a comma, wrap the whole field in double quotes:

```csv
8441,20F9,D,GJ Fire Disp,"Fire dispatch, primary",Fire Dispatch,Fire,1
```

Then apply it:

```bash
docker compose restart trunk-recorder
```

New calls pick up the names immediately. Calls already recorded keep whatever
name they had at the time — the name is stored with each call as it happens.

---

**Next:** [5. Tuning transcription](05-tuning-whisper.md)
