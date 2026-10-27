#!/usr/bin/env python3
"""
ShuiCast stability fixes, applied at build time.

  1. fflush      - the recording archive file is flushed after every write, so a crash
                   or power loss does not lose the last buffered part of the recording.
                   (MP3/Ogg archive in SendToServer and the four WAV archive writes.)
  2. recording   - when the recording device is lost (RDP / Terminal Server session
     recovery      change, USB device unplugged, driver reset) the BASS record channel
                   stops and nothing restarts it. A watcher (called from the existing
                   1 second timer) now re-opens the device, but only every
                   RETRY_INTERVAL_MS and at most MAX_TRIES times in a row. After the
                   last try one status message is shown and it stops. Selecting the
                   device again (or any successful restart) re-arms it.
                   Standalone build only (SHUICASTSTANDALONE).

(The password fix of the AltaCast patch is NOT needed: ShuiCast's URLize() encodes into
 a new buffer and never touches m_Password.)

Usage:  python shuicast_fixes.py <path to trunk>

Idempotent (already applied -> skipped). If an anchor cannot be found the script stops
with an error. Can run before or after shuicast_patch.py / shuicast_dim.py.
"""
import re
import sys
from pathlib import Path

# ---- tuning ---------------------------------------------------------------
RETRY_INTERVAL_MS = 3000   # wait between two attempts to re-open the device
MAX_TRIES = 20             # attempts in a row before giving up (20 x 3 s = about 1 minute)
# ---------------------------------------------------------------------------

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else ".")


def fail(msg):
    print("shuicast_fixes: ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


def sub_n(text, pattern, repl, label, count=1, flags=0):
    new, n = re.subn(pattern, repl, text, flags=flags)
    if n != count:
        fail("%s: expected exactly %d match(es), found %d" % (label, count, n))
    return new


def patch_file(rel, func):
    path = SRC / rel
    if not path.exists():
        fail("%s not found under %s" % (rel, SRC))
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("latin-1").replace("\r\n", "\n")
    new = func(text)
    if new == text:
        return
    if crlf:
        new = new.replace("\n", "\r\n")
    path.write_bytes(new.encode("latin-1"))


# ------------------------------------------------------------------ 1. fflush
MARK_FFLUSH = "shuicast_fixes: fflush"


def fix_fflush(t):
    if MARK_FFLUSH in t:
        print("shuicast_fixes: fflush already present, skipped")
        return t
    # MP3 / Ogg archive (one-line if in SendToServer)
    t = sub_n(
        t,
        r"([ \t]*)if \( m_SaveDirectoryFlag && m_SaveFilePtr && !m_SaveAsWAV \) fwrite\( data, length, 1, m_SaveFilePtr \);\n",
        lambda m: m.group(0)
        + m.group(1)
        + "if ( m_SaveDirectoryFlag && m_SaveFilePtr && !m_SaveAsWAV ) fflush( m_SaveFilePtr );\t/* "
        + MARK_FFLUSH + " */\n",
        "libshuicast.cpp archive fwrite (MP3/Ogg)",
    )
    # WAV archive (three sample writes, each inside braces)
    t = sub_n(
        t,
        r"([ \t]*)fwrite\( int_samples, sizeofData, 1, m_SaveFilePtr \);\n",
        lambda m: m.group(0) + m.group(1) + "fflush( m_SaveFilePtr );\n",
        "libshuicast.cpp archive fwrite (WAV)",
        count=4,
    )
    print("shuicast_fixes: fflush added (5 places)")
    return t


# ---------------------------------------------------------------- 2. recording
MARK_REC = "shuicast_fixes: recording device recovery"

H_DECL = "    void RecoverRecording();\t// " + MARK_REC + "\n"

CPP_CALL = "    pWindow->RecoverRecording();\t/* " + MARK_REC + " */\n"

CPP_DEF = """/*
 * shuicast_fixes: recording device recovery (standalone build only).
 * Called once a second. When the recording device is lost the BASS record channel
 * stops while m_LiveRecRunning is still set. Re-open the device, but only every
 * RETRY_MS and at most MAX_TRIES times in a row, so a device that stays away does not
 * cause a flood of init attempts and status messages. A successful restart (by this
 * function or by the user selecting the device again) re-arms it.
 */
void CMainWindow::RecoverRecording ()
{
#ifdef SHUICASTSTANDALONE
    const DWORD RETRY_MS  = %(retry)d;
    const int   MAX_TRIES = %(tries)d;
    static bool  lost = false;
    static bool  gaveUp = false;
    static int   tries = 0;
    static DWORD lastTry = 0;

    if ( !::IsWindow( m_hWnd ) ) return;

    if ( !lost )
    {
        if ( m_LiveRecRunning && BASS_ChannelIsActive( inRecHandle ) == BASS_ACTIVE_STOPPED )
        {
            lost = true;
            gaveUp = false;
            tries = 0;
            generalStatusCallback( (void *) "Recording device lost - trying to recover", FILE_LINE );
        }
        else
        {
            return;
        }
    }
    else if ( BASS_ChannelIsActive( inRecHandle ) != BASS_ACTIVE_STOPPED )
    {
        lost = false;
        gaveUp = false;
        tries = 0;
        return;
    }

    if ( gaveUp ) return;

    if ( tries >= MAX_TRIES )
    {
        gaveUp = true;
        generalStatusCallback( (void *) "Recording device lost - gave up retrying. Select the device again to resume.", FILE_LINE );
        return;
    }

    if ( tries > 0 && GetTickCount() - lastTry < RETRY_MS ) return;

    tries++;
    lastTry = GetTickCount();
    StopRecording();
    StartRecording();

    if ( BASS_ChannelIsActive( inRecHandle ) != BASS_ACTIVE_STOPPED )
    {
        lost = false;
        gaveUp = false;
        tries = 0;
        generalStatusCallback( (void *) "Recording device recovered", FILE_LINE );
    }
#endif
}

""" % {"retry": RETRY_INTERVAL_MS, "tries": MAX_TRIES}


def fix_rec_header(t):
    if MARK_REC in t:
        print("shuicast_fixes: header already patched, skipped")
        return t
    return sub_n(
        t,
        r"([ \t]*void DoStartRecording\( bool restart = false \);\n)",
        lambda m: m.group(1) + H_DECL,
        "MainWindow.h DoStartRecording declaration",
    )


def fix_rec_cpp(t):
    if MARK_REC in t:
        print("shuicast_fixes: recording recovery already present, skipped")
        return t
    t = sub_n(
        t,
        r"([ \t]*if \( needRestart \) pWindow->DoStartRecording\( true \);\n)",
        lambda m: m.group(1) + CPP_CALL,
        "MainWindow.cpp MetadataCheckTimer",
    )
    t = sub_n(
        t,
        r"(/\* CMainWindow dialog \*/\n)",
        lambda m: CPP_DEF + m.group(1),
        "MainWindow.cpp CMainWindow dialog marker",
    )
    print("shuicast_fixes: recording recovery added (retry every %d ms, max %d tries)"
          % (RETRY_INTERVAL_MS, MAX_TRIES))
    return t


if not CPP_DEF.isascii():
    fail("internal: inserted C++ must be ASCII")

patch_file("libshuicast/libshuicast.cpp", fix_fflush)
patch_file("shuicast/MainWindow.h", fix_rec_header)
patch_file("shuicast/MainWindow.cpp", fix_rec_cpp)
print("shuicast_fixes: done")
