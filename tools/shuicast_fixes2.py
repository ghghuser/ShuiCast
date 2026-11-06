#!/usr/bin/env python3
"""
shuicast_fixes2.py - second batch of ShuiCast fixes (applied at build time).

  1. Encoder lock while connected
       Changing Encoder Type / Bitrate / Quality / Samplerate / Channels in the settings
       dialog while an encoder is connected re-initialised the running encoder and crashed.
       While the encoder is connected these fields are now greyed out.
  2. Multiple instances
       A warning popup (Yes/No, default No) is shown when another instance that uses the
       same folder (= the same .cfg files) is already running.
  3. HE-AAC High / LC-AAC: the channel mode written to the ini file was empty (only
       HE-AAC set it), so the encoder refused to start ("Encoder init failed").
       It is now always set (mono 1 / stereo 2). The real reason is logged, and the
       status line says "AAC+ init failed (see log)".

Usage:  python shuicast_fixes2.py trunk

Idempotent. Stops with an error if an anchor is missing. Can run before or after
shuicast_fixes.py / shuicast_patch.py / shuicast_dim.py.
"""
import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
TAG = "shuicast_fixes2"


def fail(msg):
    print(TAG + ": ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


def sub_once(text, pattern, repl, label, flags=0):
    new, n = re.subn(pattern, repl, text, flags=flags)
    if n != 1:
        fail("%s: expected exactly 1 match, found %d" % (label, n))
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
    print(TAG + ": patched " + rel)


def c_wide(s):
    """Python str -> body of a C++ wide string literal, ASCII only (\\uXXXX escapes)."""
    out = []
    for ch in s:
        if ch == "\n":
            out.append("\\n")
        elif ch in '"\\':
            out.append("\\" + ch)
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append("\\u%04x" % ord(ch))
    return "".join(out)


# ------------------------------------------------------------ 1. encoder lock
MARK_LOCK = TAG + ": lock"


def fix_lock_header(t):
    if MARK_LOCK in t:
        return t
    return sub_once(
        t,
        r"([ \t]*)void UpdateFields\s*\(\);\n",
        lambda m: m.group(0)
        + m.group(1) + "void UpdateFieldsCore();   // %s\n" % MARK_LOCK
        + m.group(1) + "void ApplyEncoderLock();\n"
        + m.group(1) + "BOOL m_LockEncoder = FALSE;   // TRUE while the encoder is connected\n",
        "BasicSettings.h UpdateFields declaration",
    )


LOCK_CODE = """void CBasicSettings::UpdateFields ()
{
	UpdateFieldsCore();
	ApplyEncoderLock();
}

// %(m)s: while connected, the fields that change the running encoder are greyed out
void CBasicSettings::ApplyEncoderLock ()
{
	CWnd *w;

	if ( !m_LockEncoder )
	{
		m_EncoderTypeCtrl.EnableWindow( TRUE );
		w = GetDlgItem( IDC_SAMPLERATE );
		if ( w != NULL ) w->EnableWindow( TRUE );
		return;
	}
	m_EncoderTypeCtrl.EnableWindow( FALSE );
	m_BitrateCtrl.EnableWindow( FALSE );
	m_QualityCtrl.EnableWindow( FALSE );
	m_UseBitrateCtrl.EnableWindow( FALSE );
	m_ChannelsCtrl.EnableWindow( FALSE );
	w = GetDlgItem( IDC_SAMPLERATE );
	if ( w != NULL ) w->EnableWindow( FALSE );
}

void CBasicSettings::UpdateFieldsCore ()
{"""


def fix_lock_cpp(t):
    if MARK_LOCK in t:
        return t
    return sub_once(
        t,
        r"void CBasicSettings::UpdateFields\s*\(\)\s*\{",
        lambda m: LOCK_CODE % {"m": MARK_LOCK},
        "BasicSettings.cpp UpdateFields",
    )


def fix_lock_config(t):
    if MARK_LOCK in t:
        return t
    return sub_once(
        t,
        r"([ \t]*)basicSettings->UpdateFields\(\);\n",
        lambda m: m.group(1)
        + "basicSettings->m_LockEncoder = (encoder->IsConnected() != 0);   // %s\n" % MARK_LOCK
        + m.group(0),
        "Config.cpp GlobalsToDialog UpdateFields",
    )


# ------------------------------------------------------- 2. multiple instances
MARK_INST = TAG + ": instance"
INST_TITLE = "多重起動の確認"
INST_MSG = (
    "同じフォルダ(設定ファイルの場所)を使うインスタンスが、すでに起動しています。\n\n"
    "設定ファイル(.cfg)を互いに上書きし合い、設定が壊れたり、"
    "意図しない内容で保存されたりするおそれがあります。\n\n"
    "それでも起動しますか?\n\n"
    "(別々に使いたい場合は、アプリのフォルダごとコピーして、別のフォルダから起動してください。)"
)


def instance_block(indent, dir_expr, prefix):
    lines = [
        "/* %s: warn when another instance uses the same folder (= the same .cfg files) */" % MARK_INST,
        "{",
        "\tchar instDir[MAX_PATH] = \"\";",
        "\tchar *instFilePart = NULL;",
        "\tchar instMutexName[MAX_PATH + 64];",
        "\tif (GetFullPathNameA(%s, MAX_PATH, instDir, &instFilePart) == 0) {" % dir_expr,
        "\t\tstrcpy(instDir, \".\");",
        "\t}",
        "\tfor (char *ip = instDir; *ip; ++ip) {",
        "\t\tif (*ip == '\\\\' || *ip == ':') *ip = '/';",
        "\t\telse if (*ip >= 'A' && *ip <= 'Z') *ip = (char)(*ip - 'A' + 'a');",
        "\t}",
        "\tsprintf(instMutexName, \"Local\\\\%s_inst_%%s\", instDir);" % prefix,
        "\tHANDLE instMutex = CreateMutexA(NULL, FALSE, instMutexName);",
        "\tif (instMutex != NULL && GetLastError() == ERROR_ALREADY_EXISTS) {",
        "\t\tif (MessageBoxW(NULL, L\"%s\", L\"%s\", MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) != IDYES) {"
        % (c_wide(INST_MSG), c_wide(INST_TITLE)),
        "\t\t\treturn FALSE;",
        "\t\t}",
        "\t}",
        "}",
    ]
    return "".join(indent + ln + "\n" for ln in lines)


def fix_instance(t):
    if MARK_INST in t:
        return t
    return sub_once(
        t,
        r"([ \t]*)AfxEnableControlContainer\(\);\n",
        lambda m: m.group(0) + "\n" + instance_block(m.group(1), "\".\"", "shuicast"),
        "shuicast_standalone.cpp AfxEnableControlContainer",
    )


# ----------------------------------------------- 3. HE-AAC High / LC-AAC (libshuicast)
MARK_AACP = TAG + ": aacp"
AACP_TYPES = ("ENCODER_AACP_HE", "ENCODER_AACP_HE_HIGH", "ENCODER_AACP_LC",
              "ENCODER_FG_AACP_AUTO", "ENCODER_FG_AACP_LC", "ENCODER_FG_AACP_HE",
              "ENCODER_FG_AACP_HEV2")


def fix_aacp(t):
    if MARK_AACP in t:
        return t
    # default channel mode, so HE-AAC High / LC-AAC never get an empty value
    t = sub_once(
        t,
        r'([ \t]*)sprintf\(bitrateValue, "%d", bitrateLong\);\n',
        lambda m: m.group(0)
        + m.group(1) + 'strcpy( channelMode, (m_CurrentChannels == 2) ? "2" : "1" );   // %s: default, no empty value\n' % MARK_AACP,
        "libshuicast.cpp aacp channel mode default",
    )
    # log the real reason
    t = sub_once(
        t,
        r'([ \t]*)LogMessage\( LOG_ERROR, "Invalid AAC\+ settings" \);\n',
        lambda m: (
            m.group(1) + "{\n"
            + m.group(1) + "\tchar aacpDiag[1024];\n"
            + m.group(1) + "\tsprintf( aacpDiag, \"Invalid AAC+ settings (section=%s channels=%d samplerate=%d bitrate=%ld channelmode=%s ini=%s)\",\n"
            + m.group(1) + "\t\tsectionName, (int)m_CurrentChannels, (int)m_CurrentSamplerate, bitrateLong, channelMode, conf_file );\n"
            + m.group(1) + "\tLogMessage( LOG_ERROR, aacpDiag );\n"
            + m.group(1) + "}\n"
        ),
        "libshuicast.cpp aacp log",
    )
    # status line: do not hide the cause behind the generic message
    cond = " || ".join("m_Type == %s" % n for n in AACP_TYPES)
    t = sub_once(
        t,
        r'([ \t]*)else SetServerStatus\( "Encoder init failed" \);\n',
        lambda m: m.group(1) + "else if ( %s ) SetServerStatus( \"AAC+ init failed (see log)\" );\n" % cond + m.group(0),
        "libshuicast.cpp init failed status",
    )
    return t


patch_file("shuicast/Config.cpp", fix_lock_config)
patch_file("shuicast/BasicSettings.h", fix_lock_header)
patch_file("shuicast/BasicSettings.cpp", fix_lock_cpp)
patch_file("shuicast/shuicast_standalone.cpp", fix_instance)
patch_file("libshuicast/libshuicast.cpp", fix_aacp)
print(TAG + ": done")
