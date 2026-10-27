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
  3. mono        - LAME (mp3) and Ogg Vorbis encoders set to Mono produced noise only /
     encoders      slow, low, distorted sound, because they ignored that the encoder core
                   always hands them interleaved stereo. Fixed in both branches.
  4. joint       - the "Joint Stereo" label vanished when the mouse was moved over it
     stereo        (the empty check box was wider than the box and sat on top of the
     label     label). The check box is narrowed in shuicast.rc.
  5. AAC         - * AAC Quality 0 / empty (what the field holds after switching the
     settings       encoder type to AAC) gave a very distorted 24 kbps stream. It is now
                    100 in the dialog and, as a safety net, in the encoder.
                  * "Use bitrate" (Ogg Vorbis only) stayed ticked after switching to AAC
                    and then disabled Quality and enabled the unused Bitrate field. For
                    AAC only Quality is editable now.
                  * Ogg Vorbis Quality is 0..10 (100 would mean maximum quality, about
                    500 kbps). The AAC value no longer carries over to Ogg, and the initial
                    Ogg quality is 3 (oggenc's default, about 112 kbps) instead of 0.
                    Change OGG_DEFAULT_QUALITY in this script to use another value.

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


# ------------------------------------------------------------ 3. mono encoders
MARK_MONO = "shuicast_fixes: mono"


def fix_mono(t):
    """DoEncoding() ALWAYS receives interleaved stereo (for a mono encoder both
    channels carry the same signal). The LAME and Ogg mono branches did not take that
    into account: LAME wrote to a fixed index (-> noise only), Ogg consumed the
    interleaved pairs as consecutive mono samples (-> half speed, low and distorted)."""
    if MARK_MONO in t:
        print("shuicast_fixes: mono fix already present, skipped")
        return t
    # LAME (lame_enc.dll): the output index was never advanced
    t = sub_n(
        t,
        r"(if \( m_CurrentChannels == 1 \)[^\n]*\n\s*\{\n)(\s*)samplecount = numsamples;\n"
        r"(\s*for \( int i = 0; i < numsamples \* 2; i \+= 2 \)\n\s*\{\n\s*)"
        r"int_samples\[samplecount\] = \(short\)\(samples\[i\] \* 32767\.0\);\n",
        lambda m: m.group(1) + m.group(2) + "samplecount = 0;\t/* " + MARK_MONO + " */\n"
        + m.group(3) + "int_samples[samplecount++] = (short)(samples[i] * 32767.0);\n",
        "libshuicast.cpp LAME mono branch",
    )
    # Ogg Vorbis: skip the duplicated right channel
    t = sub_n(
        t,
        r"(if \( m_CurrentChannels == 1 \)\n\s*\{\n\s*while \( samplecounter < numsamples \)\n\s*\{\n)"
        r"(\s*)buffer\[0\]\[samplecounter\] = \*\(src\+\+\);\n",
        lambda m: m.group(1) + m.group(2) + "buffer[0][samplecounter] = *(src++);\n"
        + m.group(2) + "src++;\t/* " + MARK_MONO + ": skip the duplicated channel */\n",
        "libshuicast.cpp Ogg mono branch",
    )
    print("shuicast_fixes: mono fix added (LAME, Ogg)")
    return t


# ------------------------------------------------- 4. Joint Stereo label (rc)
MARK_JS = "shuicast_fixes: joint stereo"


def fix_jointstereo_rc(t):
    """The empty check box (62 dialog units wide) lies on top of the 'Joint Stereo' label.
    Whenever the mouse hovers over it, the check box repaints its whole area and erases the
    label until the dialog is reopened. Make the check box as wide as the box itself."""
    pat = r'(CONTROL\s+"",IDC_JOINTSTEREO,"Button",BS_AUTOCHECKBOX \| WS_TABSTOP,249,64,)62(,10)'
    if not re.search(pat, t):
        if re.search(r'IDC_JOINTSTEREO,"Button",BS_AUTOCHECKBOX \| WS_TABSTOP,249,64,12,10', t):
            print("shuicast_fixes: joint stereo rc already patched, skipped")
            return t
        fail("shuicast.rc: IDC_JOINTSTEREO check box line not found")
    t = sub_n(t, pat, r"\g<1>12\g<2>", "shuicast.rc IDC_JOINTSTEREO width")
    print("shuicast_fixes: joint stereo check box narrowed (62 -> 12)")
    return t


# ---------------------------------------------------------------- 2. recording
MARK_REC ="shuicast_fixes: recording device recovery"

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

# ----------------------------------------------------------- 5. AAC settings
MARK_AAC = "shuicast_fixes: aac"

AAC_UI_NEW = """m_BitrateCtrl.EnableWindow( FALSE );\t/* %(m)s: faac is quality based, the bitrate and
        "Use bitrate" (Ogg only) are ignored - only Quality is editable */
        m_QualityCtrl.EnableWindow( TRUE );
        if ( m_QualityCtrl.GetSafeHwnd() )
        {
            CString aacQuality;
            m_QualityCtrl.GetWindowText( aacQuality );
            if ( atoi( LPCSTR( aacQuality ) ) <= 0 )  // empty/0 after switching from another type
            {
                m_QualityCtrl.SetWindowText( _T( "100" ) );
                m_Quality = _T( "100" );
            }
        }
""" % {"m": MARK_AAC}


def fix_aac_ui(t):
    if MARK_AAC in t:
        print("shuicast_fixes: AAC settings (UI) already patched, skipped")
        return t
    t = sub_n(
        t,
        r'(else if \(m_EncoderType == "AAC"\)\n\s*\{\n\s*)'
        r"m_BitrateCtrl\.EnableWindow\(\s*m_UseBitrate \);\n\s*m_QualityCtrl\.EnableWindow\(\s*!m_UseBitrate \);\n",
        lambda m: m.group(1) + AAC_UI_NEW,
        "BasicSettings.cpp AAC branch of UpdateFields",
    )
    print("shuicast_fixes: AAC settings (UI) fixed: Quality only, empty/0 -> 100")
    return t


def fix_aac_quality(t):
    if MARK_AAC in t:
        print("shuicast_fixes: AAC quality default already patched, skipped")
        return t
    t = sub_n(
        t,
        r"([ \t]*)m_pConfig->quantqual = atoi\( m_AACQuality \);\n",
        lambda m: m.group(1) + "m_pConfig->quantqual = ( atoi( m_AACQuality ) > 0 ) ? atoi( m_AACQuality ) : 100;\t/* "
        + MARK_AAC + ": 0/empty -> 100 */\n",
        "libshuicast.cpp quantqual",
    )
    print("shuicast_fixes: AAC quality 0/empty -> 100 in the encoder")
    return t


# Ogg Vorbis Quality is 0..10 (x0.1 -> libvorbis 0.0..1.0; anything >= 10 is clamped to the
# maximum, about 500 kbps). The Quality field is shared with AAC (10..500), so the value 100
# set for AAC must not carry over to Ogg. Ogg default: 3 = what oggenc uses (about 112 kbps).
OGG_DEFAULT_QUALITY = "3"
MARK_OGG = "shuicast_fixes: ogg"

OGG_UI_BLOCK = """        /* %(m)s: Quality is 0..10 here. An empty value or the AAC value (100)
         * must not carry over - 10 or more means maximum quality (about 500 kbps). */
        if ( m_QualityCtrl.GetSafeHwnd() )
        {
            CString oggQuality;
            m_QualityCtrl.GetWindowText( oggQuality );
            if ( oggQuality.IsEmpty() || atof( LPCSTR( oggQuality ) ) < 0 || atof( LPCSTR( oggQuality ) ) > 10 )
            {
                m_QualityCtrl.SetWindowText( _T( "%(q)s" ) );
                m_Quality = _T( "%(q)s" );
            }
        }
""" % {"m": MARK_OGG, "q": OGG_DEFAULT_QUALITY}


def fix_ogg_ui(t):
    if MARK_OGG in t:
        print("shuicast_fixes: Ogg quality (UI) already patched, skipped")
        return t
    t = sub_n(
        t,
        r'(else if \(m_EncoderType == "OggVorbis"\)\n\s*\{\n\s*m_UseBitrateCtrl\.EnableWindow\(TRUE\);\n)',
        lambda m: m.group(1) + OGG_UI_BLOCK,
        "BasicSettings.cpp OggVorbis branch of UpdateFields",
    )
    print("shuicast_fixes: Ogg quality kept within 0..10 (fallback %s)" % OGG_DEFAULT_QUALITY)
    return t


def fix_ogg_default(t):
    """Initial Ogg quality (no config file yet): 0 (about 64 kbps) -> OGG_DEFAULT_QUALITY."""
    if re.search(r'"OggQuality", "%s"' % re.escape(OGG_DEFAULT_QUALITY), t):
        print("shuicast_fixes: Ogg default quality already %s, skipped" % OGG_DEFAULT_QUALITY)
        return t
    t = sub_n(
        t,
        r'("OggQuality", )"0"',
        lambda m: m.group(1) + '"%s"' % OGG_DEFAULT_QUALITY,
        "libshuicast.cpp OggQuality default",
    )
    print("shuicast_fixes: Ogg default quality 0 -> %s" % OGG_DEFAULT_QUALITY)
    return t


patch_file("libshuicast/libshuicast.cpp",
           lambda t: fix_ogg_default(fix_aac_quality(fix_mono(fix_fflush(t)))))
patch_file("shuicast/BasicSettings.cpp", lambda t: fix_ogg_ui(fix_aac_ui(t)))
patch_file("shuicast/shuicast.rc", fix_jointstereo_rc)
patch_file("shuicast/MainWindow.h", fix_rec_header)
patch_file("shuicast/MainWindow.cpp", fix_rec_cpp)
print("shuicast_fixes: done")
