# Why your antivirus flags this app, and how to allow it

Some antivirus tools, including Microsoft Defender, may flag `UVCarPlay.exe`
as a virus or trojan, often with a generic name such as `Trojan:Win32/Wacatac`
or `Program:Win32/Wacapew`. **This is a false positive.** The app is not a
trojan. This page explains why the warning appears and how to let the app run.

If you would rather not override your antivirus, you do not have to. You can
instead do the change by hand with adb, following the steps in the main
[README](README.md).

---

## Why it happens

The warning is about how the app is built and what it does, not about any real
malware in it. Several things make antivirus heuristics suspicious:

- **It is a packaged Python app.** The app is built with Nuitka, which bundles
  a Python runtime into one `.exe`. Many real viruses are packaged the same
  way, so scanners often flag the shape of the file rather than its behaviour.
- **It searches your network.** To find the dash, the app checks other devices
  on your Wi-Fi for an open port. Scanning the local network looks like the
  behaviour of a worm to an antivirus, even though here it only looks for your
  dash.
- **It downloads and runs a helper tool.** On first run the app downloads
  Google's official `adb` (Android platform-tools) and runs it to talk to the
  dash. Downloading and running another program is a pattern scanners watch
  for.
- **It is new and unsigned.** The app is not code-signed, and few people have
  downloaded it, so your antivirus has no reputation history for it and errs on
  the side of blocking.

None of these are harmful here, but together they trip the automatic checks.

---

## How to allow the app

### Microsoft Defender (Windows 10 / 11)

**If Windows removed or quarantined the file, restore it first:**

1. Open **Windows Security** (search for it in the Start menu).
2. Go to **Virus & threat protection**.
3. Under **Current threats**, click **Protection history**.
4. Find the `UVCarPlay.exe` item, open it, and choose **Restore** (you may
   need **Actions → Restore**).

**Then add an exclusion so it is not flagged again:**

1. In **Virus & threat protection**, click **Manage settings** under
   *Virus & threat protection settings*.
2. Scroll to **Exclusions** and click **Add or remove exclusions**.
3. Click **Add an exclusion**, then choose **File**.
4. Select the `UVCarPlay.exe` you downloaded.

The app will now run without being flagged.

**SmartScreen ("Windows protected your PC"):** if you see this blue window when
you open the app, click **More info**, then **Run anyway**. It appears because
the app is not code-signed.

### Other antivirus programs

The steps differ by product, but the idea is the same: **restore the file from
quarantine, then add it to the allow list (also called exclusions, exceptions,
or whitelist).** Look in your antivirus settings for:

- **Quarantine** or **Protection history** — to restore the file.
- **Exclusions**, **Exceptions**, or **Allowed threats** — to add
  `UVCarPlay.exe` so it is not flagged again.

Common names: Avast / AVG ("Exceptions"), Norton ("Exclusions / low risks"),
McAfee ("Excluded files"), Bitdefender ("Exceptions"), Kaspersky ("Trusted
applications").

---

## How to be sure it is safe

If you want to check the file before allowing it:

- **Scan it on VirusTotal.** Upload `UVCarPlay.exe` at
  [virustotal.com](https://www.virustotal.com). It runs many scanners at once.
  A packaged app like this often shows a few engines flagging it while most do
  not; that pattern is typical of a false positive.
- **Build it yourself.** The full source is in this repo. You can read it and
  build the `.exe` with the steps in the GitHub Actions workflow
  (`.github/workflows/build-windows.yml`), so you never run a binary you did
  not make.

---

## Reporting the false positive

Reporting helps get the warning removed for everyone:

- **Microsoft Defender:** submit the file at
  [microsoft.com/wdsi/filesubmission](https://www.microsoft.com/wdsi/filesubmission)
  as a **false positive**. Microsoft usually reviews it within a few days.
- **Other vendors** have similar false-positive submission pages on their
  websites.
