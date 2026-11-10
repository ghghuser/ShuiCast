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


# ------------------------------------------- 5. unavailable encoders stay in the list
MARK_AVAIL = TAG + ": avail"

AVAIL_CODE = r'''/* @@M@@: encoder types whose DLL cannot be loaded stay in the list and are marked "(DLL...)".
 * Selecting one shows the reason (missing / not 32bit / missing dependency). */
#define ENC_NA_SUFFIX " (DLL\x82\xc8\x82\xb5)"
#define ENC_ALL_PLUGINS 1

static BOOL EncFileExists(const CString &path)
{
	return GetFileAttributes(path) != INVALID_FILE_ATTRIBUTES;
}

static DWORD EncRvaToOffset(IMAGE_NT_HEADERS32 *nt, DWORD rva)
{
	IMAGE_SECTION_HEADER *sec = IMAGE_FIRST_SECTION(nt);
	WORD i;

	for (i = 0; i < nt->FileHeader.NumberOfSections; i++) {
		DWORD size = sec[i].Misc.VirtualSize;
		if (sec[i].SizeOfRawData > size) size = sec[i].SizeOfRawData;
		if (rva >= sec[i].VirtualAddress && rva < sec[i].VirtualAddress + size) {
			return rva - sec[i].VirtualAddress + sec[i].PointerToRawData;
		}
	}
	return 0;
}

/* names of the DLLs that the given (32bit) DLL imports directly but that cannot be found */
static CString EncFindMissingImports(const CString &dllPath)
{
	CString missing;
	CString dllDir = dllPath;
	int slash = dllDir.ReverseFind('\\');
	HANDLE hf;
	DWORD size;
	DWORD got = 0;
	BYTE *buf = NULL;

	if (slash >= 0) dllDir = dllDir.Left(slash + 1);
	else dllDir = "";

	hf = CreateFile(dllPath, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
	if (hf == INVALID_HANDLE_VALUE) return missing;
	size = GetFileSize(hf, NULL);
	if (size != INVALID_FILE_SIZE && size > 1024 && size < 64 * 1024 * 1024) {
		buf = (BYTE *) malloc(size);
	}
	if (buf != NULL && ReadFile(hf, buf, size, &got, NULL) && got == size) {
		IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *) buf;
		if (dos->e_magic == IMAGE_DOS_SIGNATURE && dos->e_lfanew > 0 &&
			(DWORD) dos->e_lfanew + sizeof(IMAGE_NT_HEADERS32) < size) {
			IMAGE_NT_HEADERS32 *nt = (IMAGE_NT_HEADERS32 *) (buf + dos->e_lfanew);
			if (nt->Signature == IMAGE_NT_SIGNATURE && nt->OptionalHeader.Magic == IMAGE_NT_OPTIONAL_HDR32_MAGIC) {
				DWORD impRva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
				DWORD impOff = (impRva != 0) ? EncRvaToOffset(nt, impRva) : 0;
				int guard = 0;
				while (impOff != 0 && impOff + sizeof(IMAGE_IMPORT_DESCRIPTOR) <= size && guard++ < 512) {
					IMAGE_IMPORT_DESCRIPTOR *d = (IMAGE_IMPORT_DESCRIPTOR *) (buf + impOff);
					DWORD nameOff;
					if (d->Name == 0) break;
					nameOff = EncRvaToOffset(nt, d->Name);
					if (nameOff != 0 && nameOff < size) {
						const char *name = (const char *) (buf + nameOff);
						DWORD len = 0;
						while (nameOff + len < size && name[len] != 0 && len < 260) len++;
						if (nameOff + len < size && name[len] == 0 && len > 0 &&
							_strnicmp(name, "api-ms-", 7) != 0 && _strnicmp(name, "ext-ms-", 7) != 0) {
							char found[MAX_PATH];
							char *filePart = NULL;
							CString own = dllDir + name;
							if (SearchPathA(NULL, name, NULL, MAX_PATH, found, &filePart) == 0 && !EncFileExists(own)) {
								if (!missing.IsEmpty()) missing += ", ";
								missing += name;
							}
						}
					}
					impOff += sizeof(IMAGE_IMPORT_DESCRIPTOR);
				}
			}
		}
	}
	if (buf != NULL) free(buf);
	CloseHandle(hf);
	return missing;
}

/* Two passes: pass 0 adds the usable encoders, pass 1 adds the unusable ones below them. */
void CBasicSettings::AddEncoderItem(const char *label, const char *dll, BOOL plugins, BOOL unavailablePass)
{
	HINSTANCE h = LoadLibrary(dll);

	if (h == NULL && plugins) {
		CString p;
		p.Format("plugins\\%s", dll);
		h = LoadLibrary(p);
	}
	if (h != NULL) {
		FreeLibrary(h);
		if (!unavailablePass) m_EncoderTypeCtrl.AddString(label);
	}
	else if (unavailablePass) {
		CString s = label;
		s += ENC_NA_SUFFIX;
		m_EncoderTypeCtrl.AddString(s);
	}
}

/* select by exact name (also the "(DLL...)" variant); SelectString would match prefixes ("AAC" / "AAC Plus") */
void CBasicSettings::SelectEncoderItem(const CString &name)
{
	int idx = m_EncoderTypeCtrl.FindStringExact(-1, name);

	if (idx == CB_ERR) {
		CString s = name;
		s += ENC_NA_SUFFIX;
		idx = m_EncoderTypeCtrl.FindStringExact(-1, s);
	}
	if (idx != CB_ERR) m_EncoderTypeCtrl.SetCurSel(idx);
}

void CBasicSettings::ShowEncoderUnavailable(const CString &selected)
{
	CString type = selected;
	int cut = type.Find(" (");
	const char *dll = "";
	BOOL plugins = (ENC_ALL_PLUGINS != 0);
	char exe[MAX_PATH] = "";
	char cwd[MAX_PATH] = "";
	CString exeDir;
	CString cwdDir;
	CString cand[4];
	CString found;
	int n = 0;
	int i;
	int sl;
	wchar_t msg[2048];
	wchar_t body[1900];

	if (cut > 0) type = type.Left(cut);
	if (type == "MP3 Lame") dll = "lame_enc.dll";
	else if (type == "AAC") dll = "libfaac.dll";
	else if (type == "AAC Plus" || type == "HE-AAC" || type == "HE-AAC High" || type == "LC-AAC") {
		dll = "enc_aacplus.dll";
		plugins = TRUE;
	}
	else if (type.Left(6) == "FHGAAC") {
		dll = "enc_fhgaac.dll";
		plugins = TRUE;
	}
	if (dll[0] == 0) return;

	GetModuleFileNameA(NULL, exe, MAX_PATH);
	exeDir = exe;
	sl = exeDir.ReverseFind('\\');
	if (sl >= 0) exeDir = exeDir.Left(sl + 1);
	else exeDir = "";
	GetCurrentDirectoryA(MAX_PATH, cwd);
	cwdDir = cwd;
	if (!cwdDir.IsEmpty() && cwdDir.Right(1) != "\\") cwdDir += "\\";

	cand[n++] = exeDir + dll;
	if (plugins) cand[n++] = exeDir + "plugins\\" + dll;
	cand[n++] = cwdDir + dll;
	if (plugins) cand[n++] = cwdDir + "plugins\\" + dll;
	for (i = 0; i < n; i++) {
		if (EncFileExists(cand[i])) {
			found = cand[i];
			break;
		}
	}

	if (found.IsEmpty()) {
		_snwprintf(body, 1899, L"\u7406\u7531: %S \u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093\u3002\n\n\u5b9f\u884c\u30d5\u30a1\u30a4\u30eb(exe)\u3068\u540c\u3058\u30d5\u30a9\u30eb\u30c0%s\u306b\u7f6e\u3044\u3066\u304f\u3060\u3055\u3044\u3002", dll, plugins ? L"\u3001\u307e\u305f\u306f plugins \u30d5\u30a9\u30eb\u30c0" : L"");
	}
	else {
		HMODULE h = LoadLibraryExA(found, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
		if (h != NULL) {
			FreeLibrary(h);
			_snwprintf(body, 1899, L"%S \u306f\u8aad\u307f\u8fbc\u3081\u307e\u3057\u305f\u3002\u8a2d\u5b9a\u753b\u9762\u3092\u958b\u304d\u76f4\u3059\u3068\u9078\u3079\u308b\u3088\u3046\u306b\u306a\u308a\u307e\u3059\u3002", dll);
		}
		else {
			DWORD err = GetLastError();
			if (err == ERROR_BAD_EXE_FORMAT) {
				_snwprintf(body, 1899, L"\u7406\u7531: %S \u306f\u898b\u3064\u304b\u308a\u307e\u3057\u305f\u304c\u300132bit\u7248\u3067\u306f\u306a\u3044\u305f\u3081\u8aad\u307f\u8fbc\u3081\u307e\u305b\u3093(\u30a8\u30e9\u30fc193)\u3002\n\n\u3053\u306e\u30a2\u30d7\u30ea\u306f32bit\u7248\u306a\u306e\u3067\u300132bit\u7248\u306e %S \u304c\u5fc5\u8981\u3067\u3059\u3002\n\n\u5834\u6240: %S", dll, dll, (LPCSTR) found);
			}
			else if (err == ERROR_MOD_NOT_FOUND) {
				CString missing = EncFindMissingImports(found);
				if (missing.IsEmpty()) {
					_snwprintf(body, 1899, L"\u7406\u7531: %S \u306f\u898b\u3064\u304b\u308a\u307e\u3057\u305f\u304c\u3001\u8aad\u307f\u8fbc\u3081\u307e\u305b\u3093(\u30a8\u30e9\u30fc126)\u3002\n\n\u3053\u306eDLL\u304c\u5fc5\u8981\u3068\u3059\u308b\u5225\u306eDLL\u304c\u8db3\u308a\u306a\u3044\u53ef\u80fd\u6027\u304c\u3042\u308a\u307e\u3059\u3002\n\n\u8db3\u308a\u306a\u3044DLL: %s\n\n\u5834\u6240: %S", dll, L"(\u7279\u5b9a\u3067\u304d\u307e\u305b\u3093\u3067\u3057\u305f\u3002VC\u30e9\u30f3\u30bf\u30a4\u30e0\u306a\u3069\u304c\u8db3\u308a\u306a\u3044\u5834\u5408\u304c\u3042\u308a\u307e\u3059)", (LPCSTR) found);
				}
				else {
					_snwprintf(body, 1899, L"\u7406\u7531: %S \u306f\u898b\u3064\u304b\u308a\u307e\u3057\u305f\u304c\u3001\u8aad\u307f\u8fbc\u3081\u307e\u305b\u3093(\u30a8\u30e9\u30fc126)\u3002\n\n\u3053\u306eDLL\u304c\u5fc5\u8981\u3068\u3059\u308b\u5225\u306eDLL\u304c\u8db3\u308a\u306a\u3044\u53ef\u80fd\u6027\u304c\u3042\u308a\u307e\u3059\u3002\n\n\u8db3\u308a\u306a\u3044DLL: %s\n\n\u5834\u6240: %S", dll, (LPCWSTR) CStringW((LPCSTR) missing), (LPCSTR) found);
				}
			}
			else {
				_snwprintf(body, 1899, L"\u7406\u7531: %S \u306e\u8aad\u307f\u8fbc\u307f\u306b\u5931\u6557\u3057\u307e\u3057\u305f(\u30a8\u30e9\u30fc%lu)\u3002\n\n\u5834\u6240: %S", dll, (unsigned long) err, (LPCSTR) found);
			}
		}
	}
	body[1899] = 0;
	_snwprintf(msg, 2047, L"\u300c%S\u300d\u306f\u4f7f\u3048\u307e\u305b\u3093\u3002\n\n%s", (LPCSTR) type, body);
	msg[2047] = 0;
	MessageBoxW(m_hWnd, msg, L"\u30a8\u30f3\u30b3\u30fc\u30c0\u30fc\u3092\u9078\u3079\u307e\u305b\u3093", MB_OK | MB_ICONINFORMATION);
}

'''

INIT_PAT = r"#if HAVE_LAME\b.*?(?=\s*return TRUE;)"
INIT_REPL = '{\n\tint encPass;\n\tfor (encPass = 0; encPass < 2; encPass++) {\n#if HAVE_LAME\n\t\tAddEncoderItem("MP3 Lame", "lame_enc.dll", TRUE, encPass != 0);   // @@M@@\n#endif\n#if HAVE_FAAC\n\t\tAddEncoderItem("AAC", "libfaac.dll", TRUE, encPass != 0);\n#endif\n#if HAVE_AACP\n\t\tAddEncoderItem("HE-AAC", "enc_aacplus.dll", TRUE, encPass != 0);\n\t\tAddEncoderItem("HE-AAC High", "enc_aacplus.dll", TRUE, encPass != 0);\n\t\tAddEncoderItem("LC-AAC", "enc_aacplus.dll", TRUE, encPass != 0);\n#endif\n#if HAVE_FHGAACP\n\t\tAddEncoderItem("FHGAAC-AUTO", "enc_fhgaac.dll", TRUE, encPass != 0);\n\t\tAddEncoderItem("FHGAAC-LC", "enc_fhgaac.dll", TRUE, encPass != 0);\n\t\tAddEncoderItem("FHGAAC-HE", "enc_fhgaac.dll", TRUE, encPass != 0);\n\t\tAddEncoderItem("FHGAAC-HEv2", "enc_fhgaac.dll", TRUE, encPass != 0);\n#endif\n\t}\n}'
C_OPEN = "//"
C_CLOSE = ""


def fix_avail_header(t):
    if MARK_AVAIL in t:
        return t
    return sub_once(
        t,
        r"([ \t]*)void UpdateFields\s*\(\);\n",
        lambda m: m.group(0)
        + m.group(1) + "void AddEncoderItem(const char *label, const char *dll, BOOL plugins, BOOL unavailablePass);   " + C_OPEN + " " + MARK_AVAIL + " " + C_CLOSE + "\n"
        + m.group(1) + "void SelectEncoderItem(const CString &name);\n"
        + m.group(1) + "void ShowEncoderUnavailable(const CString &selected);\n",
        "BasicSettings.h avail declarations",
    )


def fix_avail_cpp(t):
    if MARK_AVAIL in t:
        return t
    # 1. helper code before OnInitDialog
    t = sub_once(
        t,
        r"BOOL CBasicSettings::OnInitDialog\s*\(\)",
        lambda m: AVAIL_CODE.replace("@@M@@", MARK_AVAIL) + m.group(0),
        "BasicSettings.cpp OnInitDialog (helpers)",
    )
    # 2. the list is built without DLL checks
    t = sub_once(t, INIT_PAT, lambda m: INIT_REPL.replace("@@M@@", MARK_AVAIL), "BasicSettings.cpp encoder list", flags=re.S)
    # 3. selecting an unavailable type: explain and go back
    t = sub_once(
        t,
        r"(void CBasicSettings::OnSelendokEncoderType\s*\(\)\s*\{\n)",
        lambda m: m.group(1)
        + "\t" + C_OPEN + " " + MARK_AVAIL + " " + C_CLOSE + "\n"
        + "\t{\n"
        + "\t\tCString selStr;\n"
        + "\t\tint selIdx = m_EncoderTypeCtrl.GetCurSel();\n"
        + "\t\tif (selIdx >= 0) {\n"
        + "\t\t\tm_EncoderTypeCtrl.GetLBText(selIdx, selStr);\n"
        + "\t\t\tif (selStr.Find(\" (\") > 0) {\n"
        + "\t\t\t\tShowEncoderUnavailable(selStr);\n"
        + "\t\t\t\tSelectEncoderItem(m_EncoderType);\n"
        + "\t\t\t\treturn;\n"
        + "\t\t\t}\n"
        + "\t\t}\n"
        + "\t}\n",
        "BasicSettings.cpp OnSelendokEncoderType",
    )
    # 4. the "(DLL...)" mark never reaches the settings
    t = sub_once(
        t,
        r"([ \t]*)//\}\}AFX_DATA_MAP\n",
        lambda m: m.group(0)
        + m.group(1) + "if (pDX->m_bSaveAndValidate) {\t" + C_OPEN + " " + MARK_AVAIL + ": drop the (DLL..) mark " + C_CLOSE + "\n"
        + m.group(1) + "\tint markPos = m_EncoderType.Find(\" (\");\n"
        + m.group(1) + "\tif (markPos > 0) m_EncoderType = m_EncoderType.Left(markPos);\n"
        + m.group(1) + "}\n",
        "BasicSettings.cpp DoDataExchange",
    )
    return t



def fix_avail_config(t):
    if "SelectEncoderItem" in t:
        return t
    return sub_once(
        t,
        r"basicSettings->m_EncoderTypeCtrl\.SelectString\(\s*0,\s*basicSettings->m_EncoderType\s*\);",
        "basicSettings->SelectEncoderItem( basicSettings->m_EncoderType );   " + C_OPEN + " " + MARK_AVAIL + " " + C_CLOSE,
        "Config.cpp encoder selection",
    )

# ------------------------------- 4. crash on reconnect: encoder object outlives its DLL
MARK_DLL = TAG + ": loaddll"


def fix_loaddll(t):
    if MARK_DLL in t:
        return t
    return sub_once(
        t,
        r"(void CEncoder::LoadDLL \( const char_t \*name, const char_t \*msg \)\s*\{\n)([ \t]*)if \( m_hDLL \) ::FreeLibrary\( m_hDLL \);",
        lambda m: m.group(1)
        + "#if HAVE_AACP\n"
        + m.group(2) + "// %s: the AAC+ encoder object lives in the DLL that is unloaded below;\n" % MARK_DLL
        + m.group(2) + "// deleting it after the unload (next connect) crashed, so delete it first\n"
        + m.group(2) + "if ( m_AACPEncoder )\n"
        + m.group(2) + "{\n"
        + m.group(2) + "    delete m_AACPEncoder;\n"
        + m.group(2) + "    m_AACPEncoder = NULL;\n"
        + m.group(2) + "}\n"
        + "#endif\n"
        + m.group(2) + "if ( m_hDLL ) ::FreeLibrary( m_hDLL );",
        "libshuicast.cpp LoadDLL",
    )


# ------------------------------------------- 6. encoder list: order, width, refresh
MARK_UI = TAG + ": ui"


def fix_unsorted_rc(t):
    if "IDC_ENCODER_TYPE" not in t:
        fail("rc: IDC_ENCODER_TYPE not found")
    t2, n = re.subn(r"(COMBOBOX\s+IDC_ENCODER_TYPE,[^\n]*?)CBS_SORT \| ", r"\1", t)
    if n > 1:
        fail("rc: CBS_SORT matched %d times" % n)
    return t2


def fix_dropdown_width(t):
    if MARK_UI in t:
        return t
    return sub_once(
        t,
        r"([ \t]*)return TRUE;([ \t]*)// return TRUE unless you set the focus to a control",
        lambda m: (
            m.group(1) + "{\t/* " + MARK_UI + ": widen the drop-down list so the (DLL...) mark is not cut off */\n"
            + m.group(1) + "\tCDC *encDC = m_EncoderTypeCtrl.GetDC();\n"
            + m.group(1) + "\tif (encDC != NULL) {\n"
            + m.group(1) + "\t\tCFont *encOldFont = encDC->SelectObject(m_EncoderTypeCtrl.GetFont());\n"
            + m.group(1) + "\t\tint encMaxW = 0;\n"
            + m.group(1) + "\t\tint encI;\n"
            + m.group(1) + "\t\tfor (encI = 0; encI < m_EncoderTypeCtrl.GetCount(); encI++) {\n"
            + m.group(1) + "\t\t\tCString encItem;\n"
            + m.group(1) + "\t\t\tm_EncoderTypeCtrl.GetLBText(encI, encItem);\n"
            + m.group(1) + "\t\t\tint encW = encDC->GetTextExtent(encItem).cx;\n"
            + m.group(1) + "\t\t\tif (encW > encMaxW) encMaxW = encW;\n"
            + m.group(1) + "\t\t}\n"
            + m.group(1) + "\t\tencDC->SelectObject(encOldFont);\n"
            + m.group(1) + "\t\tm_EncoderTypeCtrl.ReleaseDC(encDC);\n"
            + m.group(1) + "\t\tm_EncoderTypeCtrl.SetDroppedWidth(encMaxW + GetSystemMetrics(SM_CXVSCROLL) + 12);\n"
            + m.group(1) + "\t}\n"
            + m.group(1) + "}\n"
        ) + m.group(0),
        "BasicSettings.cpp OnInitDialog end",
    )


def fix_selendok_refresh(t):
    mark = TAG + ": refresh"
    if mark in t:
        return t
    return sub_once(
        t,
        r"(void CBasicSettings::OnSelendokEncoderType\s*\(\)\s*\{(?:(?!\nvoid CBasicSettings::).)*?)(\n[ \t]*)UpdateFields\(\);([ \t]*\n\s*\})",
        lambda m: m.group(1) + m.group(2) + "UpdateFields();" + m.group(2)
        + "UpdateData( FALSE );   " + C_OPEN + " " + mark + ": show the values UpdateFields() corrected (bitrate / channels) " + C_CLOSE
        + m.group(3),
        "BasicSettings.cpp OnSelendokEncoderType end",
        flags=re.S,
    )


# ---------------------------------- 7. HE-AAC High is stereo only; LC-AAC label
MARK_HIGH = TAG + ": high"


def fix_high_header(t):
    if MARK_HIGH in t:
        return t
    return sub_once(
        t,
        r"([ \t]*)void SelectEncoderItem\(const CString &name\);\n",
        lambda m: m.group(0)
        + m.group(1) + "CString m_SavedChannels;   // %s: channels value kept while HE-AAC High forces stereo\n" % MARK_HIGH,
        "BasicSettings.h SelectEncoderItem declaration",
    )


def fix_high_cpp(t):
    if MARK_HIGH in t:
        return t
    # HE-AAC High: the encoder always produces stereo -> show 2 and grey the field
    t = sub_once(
        t,
        r'if\(br < 96 && ch == 2\)\s*\{\s*m_Channels = "1";\s*ch = 1;\s*m_ChannelsCtrl\.EnableWindow\(FALSE\);\s*\}\s*else if\(br > 160 && ch == 1\)\s*\{\s*m_Channels = "2";\s*ch = 2;\s*m_ChannelsCtrl\.EnableWindow\(FALSE\);\s*\}',
        lambda m: (
            '// %s: the HE-AAC High encoder always produces stereo\n' % MARK_HIGH
            + '\t\tm_Channels = "2";\n'
            + '\t\tch = 2;\n'
            + '\t\tm_ChannelsCtrl.EnableWindow(FALSE);'
        ),
        "BasicSettings.cpp HE-AAC High channels",
    )
    # remember the user's channels value while High is selected
    t = sub_once(
        t,
        r"([ \t]*)m_EncoderType = selectedString;\n",
        lambda m: (
            m.group(1) + "// %s: keep the user's Channels value while HE-AAC High forces 2\n" % MARK_HIGH
            + m.group(1) + 'if ( m_EncoderType == "HE-AAC High" && selectedString != "HE-AAC High" )\n'
            + m.group(1) + "{\n"
            + m.group(1) + "    if ( !m_SavedChannels.IsEmpty() ) m_Channels = m_SavedChannels;\n"
            + m.group(1) + "}\n"
            + m.group(1) + 'else if ( m_EncoderType != "HE-AAC High" && selectedString == "HE-AAC High" )\n'
            + m.group(1) + "{\n"
            + m.group(1) + "    m_SavedChannels = m_Channels;\n"
            + m.group(1) + "}\n"
        ) + m.group(0),
        "BasicSettings.cpp remember channels",
    )
    return t


def fix_lc_label(t):
    mark = TAG + ": lclabel"
    if mark in t:
        return t
    return sub_once(
        t,
        r'(strcpy\(enc, "LC-AAC\(ct\)"\);\n[ \t]*)strcpy\(mode, "Stereo"\);',
        lambda m: m.group(1) + 'strcpy(mode, (m_CurrentChannels == 2) ? "Stereo" : "Mono");   // %s' % mark,
        "libshuicast.cpp LC-AAC label",
    )


# ------------------------------- 8. "Parametric Stereo" label overlapped the checkbox
def fix_para_label(t):
    if "IDC_PARASTEREOLABEL" not in t:
        fail("rc: IDC_PARASTEREOLABEL not found")
    # the label started 1 dialog unit inside the checkbox (which ends at x=261) and lost part of the "P"
    return re.sub(r"(IDC_PARASTEREOLABEL,)260(,64,60,10)", r"\g<1>261\g<2>", t)


# ------------------------------------------------ 9. connection time column
MARK_TIME = TAG + ": time"


def fix_time_header(t):
    if MARK_TIME in t:
        return t
    return sub_once(
        t,
        r"([ \t]*)afx_msg void OnTimer\s*\(\s*UINT nIDEvent\s*\);\n",
        lambda m: m.group(0) + m.group(1) + "void UpdateConnectionTimes();   // %s\n" % MARK_TIME,
        "MainWindow.h OnTimer declaration",
    )


TIME_FUNC = '// %s: shows how long each encoder has been connected (hh:mm:ss) in the list\nvoid CMainWindow::UpdateConnectionTimes ()\n{\n    static ULONGLONG  connStart[MAX_ENCODERS];\n    static BOOL       wasConnected[MAX_ENCODERS];\n    int               numItems = m_Encoders.GetItemCount();\n    int               i;\n    static int        widenedBy = 0;   // extra characters (100 hours or more) the column was widened for\n\n    for ( i = 0; i < numItems && i < MAX_ENCODERS; i++ )\n    {\n        BOOL     connected = %s;\n        CString  text;\n\n        if ( connected && !wasConnected[i] )\n        {\n            connStart[i] = GetTickCount64();\n        }\n        wasConnected[i] = connected;\n        if ( connected )\n        {\n            ULONGLONG sec = ( GetTickCount64() - connStart[i] ) / 1000;\n            text.Format( "%%02u:%%02u:%%02u", (unsigned int)( sec / 3600 ), (unsigned int)( ( sec / 60 ) %% 60 ), (unsigned int)( sec %% 60 ) );\n        }\n        if ( m_Encoders.GetItemText( i, %d ) != text )\n        {\n            m_Encoders.SetItemText( i, %d, text );\n        }\n        if ( text.GetLength() > 8 && text.GetLength() - 8 > widenedBy )\n        {\n            m_Encoders.SetColumnWidth( %d, m_Encoders.GetColumnWidth( %d ) + ( text.GetLength() - 8 - widenedBy ) * 8 );\n            widenedBy = text.GetLength() - 8;\n        }\n    }\n}\n\n'
TIME_EARLY = '    if ( nIDEvent == 74 )   // %s: connection time\n    {\n        UpdateConnectionTimes();\n        return;\n    }\n'
TIME_COLS = 'm_Encoders.InsertColumn( 2, "Mount" );\n    m_Encoders.InsertColumn( 3, "Time" );   // %s: connection time\n    m_Encoders.SetColumnWidth( 0, 150 );\n    m_Encoders.SetColumnWidth( 1, 95 );\n    m_Encoders.SetColumnWidth( 2, 86 );\n    m_Encoders.SetColumnWidth( 3, 64 );\n    SetTimer( 74, 1000, 0 );   // once a second: update the connection time'


def fix_time_cpp(t):
    if MARK_TIME in t:
        return t
    t = sub_once(
        t,
        r"m_Encoders\.InsertColumn\( 2, \"Mount\" \);\s*m_Encoders\.SetColumnWidth\( 0, 190 \);\s*m_Encoders\.SetColumnWidth\( 1, 110 \);\s*m_Encoders\.SetColumnWidth\( 2, 96 \);",
        lambda m: TIME_COLS % MARK_TIME,
        "MainWindow.cpp list columns",
    )
    t = sub_once(
        t,
        r"void CMainWindow::OnTimer \( UINT nIDEvent \)\s*\{\n",
        lambda m: (TIME_FUNC % (MARK_TIME, '(gEncoders[i] != NULL && gEncoders[i]->IsConnected())', 3, 3, 3, 3)) + 'void CMainWindow::OnTimer ( UINT nIDEvent )\n{\n' + (TIME_EARLY % MARK_TIME),
        "MainWindow.cpp OnTimer",
    )
    return t


# ------------------------------ 10. list state: widths, order, check boxes, window size
MARK_COLW = TAG + ": colw"
LIST_VARS = ["ListColumnWidths", "ListColumnOrder", "ListChecked", "WindowSize"]
LIST_MEMB = ['m_ListColumnWidths', 'm_ListColumnOrder', 'm_ListChecked', 'm_WindowSize']


def _colw_members(m):
    # the members are public (MainWindow uses them); the access of the code that follows is restored
    labels = re.findall(r"^[ \t]*(public|private|protected):", m.string[: m.start()], re.M)
    prev = labels[-1] if labels else "private"
    ind = m.group(1)
    return (
        ind + "public:   // " + MARK_COLW + "\n"
        + ind + 'char                  m_ListColumnWidths[64]    = "";\n'
        + ind + 'char                  m_ListColumnOrder[64]     = "";\n'
        + ind + 'char                  m_ListChecked[128]        = "";\n'
        + ind + 'char                  m_WindowSize[32]          = "";\n'
        + ind + prev + ":\n"
    )


def fix_colw_libheader(t):
    if MARK_COLW in t:
        return t
    return sub_once(
        t,
        r"([ \t]*)int[ \t]+m_SkipCloseWarning[ \t]*= 0;\n",
        lambda m: m.group(0) + _colw_members(m),
        "libheader members",
    )


def fix_colw_lib(t):
    if MARK_COLW in t:
        return t
    load = ""
    store = ""
    add = ""
    for v, mb in zip(LIST_VARS, LIST_MEMB):
        load += '        GetConfigVariable( gAppName, "%s", "", %s, sizeof( %s ), NULL );\n' % (v, mb, mb)
        store += '    PutConfigVariable( gAppName, "%s", %s );\n' % (v, mb)
        add += '    AddConfigVariable( "%s" );\n' % v
    t = sub_once(
        t,
        r'([ \t]*)m_SkipCloseWarning = GetConfigVariable\( gAppName, "SkipCloseWarning", 0, desc \);\n',
        lambda m: m.group(0) + m.group(1) + '// %s: remembered list state (widths, order, checks, window size)\n' % MARK_COLW + load,
        "lib load",
    )
    t = sub_once(
        t,
        r'([ \t]*)PutConfigVariable\( gAppName, "SkipCloseWarning", m_SkipCloseWarning \);\n',
        lambda m: m.group(0) + store,
        "lib store",
    )
    t, n = re.subn(
        r'([ \t]*)AddConfigVariable\( "AutoConnect" \);\n',
        lambda m: m.group(0) + add,
        t,
    )
    if n != 1:
        fail("lib AddConfigVariable AutoConnect: expected 1, found %d" % n)
    return t


UI3_STATICS = '/* shuicast_fixes2: colw: list state kept in the config file (column widths / order, checked encoders, window size) */\n#define LIST_NUMCOLS 5\nstatic bool\ts_encUnchecked[MAX_ENCODERS + 1];\nstatic bool\ts_syncingChecks = false;\nstatic bool\ts_layoutReady = false;\nstatic RECT\ts_rcList, s_rcMeta, s_rcStatus;\nstatic int\ts_initCx = 0, s_initCy = 0, s_minWinW = 0, s_minWinH = 0;\n\nstatic bool IsEncoderChecked(int i) {\n\tif (i < 0 || i >= MAX_ENCODERS) {\n\t\treturn true;\n\t}\n\treturn !s_encUnchecked[i];\n}\n\n'

UI3_MEMBERS = '/* shuicast_fixes2: colw: restore / save the list state (widths, order, checked encoders, window size) */\nvoid CMainWindow::ApplyListState() {\n\tconst char\t*p;\n\tint\t\t\ti;\n\tint\t\t\torder[LIST_NUMCOLS];\n\tint\t\t\tused[LIST_NUMCOLS];\n\tint\t\t\tn = 0;\n\tint\t\t\tok = 1;\n\n\t/* widths: "a,b,c,..." */\n\tp = gMain.m_ListColumnWidths;\n\ti = 0;\n\twhile (i < LIST_NUMCOLS && *p) {\n\t\tint v = atoi(p);\n\t\tif (v >= 20 && v <= 800) {\n\t\t\tm_Encoders.SetColumnWidth(i, v);\n\t\t}\n\t\ti++;\n\t\tp = strchr(p, \',\');\n\t\tif (p == NULL) {\n\t\t\tbreak;\n\t\t}\n\t\tp++;\n\t}\n\n\t/* order: a permutation of 0..LIST_NUMCOLS-1 with column 0 (the check box column) first */\n\tmemset(used, 0, sizeof(used));\n\tp = gMain.m_ListColumnOrder;\n\twhile (n < LIST_NUMCOLS && *p) {\n\t\tint v = atoi(p);\n\t\tif (v < 0 || v >= LIST_NUMCOLS || used[v]) {\n\t\t\tok = 0;\n\t\t\tbreak;\n\t\t}\n\t\tused[v] = 1;\n\t\torder[n++] = v;\n\t\tp = strchr(p, \',\');\n\t\tif (p == NULL) {\n\t\t\tbreak;\n\t\t}\n\t\tp++;\n\t}\n\tif (ok && n == LIST_NUMCOLS && order[0] == 0) {\n\t\tm_Encoders.SetColumnOrderArray(LIST_NUMCOLS, order);\n\t}\n\n\t/* checked encoders: one character per encoder, 1 = checked, 0 = not checked */\n\tp = gMain.m_ListChecked;\n\ti = 0;\n\tfor (; *p && i < MAX_ENCODERS; p++) {\n\t\tif (*p == \'0\') {\n\t\t\ts_encUnchecked[i++] = true;\n\t\t}\n\t\telse if (*p == \'1\') {\n\t\t\ts_encUnchecked[i++] = false;\n\t\t}\n\t}\n}\n\nvoid CMainWindow::SaveListState() {\n\tint\t\t\t\ti;\n\tint\t\t\t\torder[LIST_NUMCOLS];\n\tchar\t\t\ttmp[32];\n\tWINDOWPLACEMENT\twp;\n\n\tgMain.m_ListColumnWidths[0] = \'\\000\';\n\tfor (i = 0; i < LIST_NUMCOLS; i++) {\n\t\tsprintf(tmp, "%s%d", (i > 0) ? "," : "", m_Encoders.GetColumnWidth(i));\n\t\tstrcat(gMain.m_ListColumnWidths, tmp);\n\t}\n\n\tif (m_Encoders.GetColumnOrderArray(order, LIST_NUMCOLS)) {\n\t\tgMain.m_ListColumnOrder[0] = \'\\000\';\n\t\tfor (i = 0; i < LIST_NUMCOLS; i++) {\n\t\t\tsprintf(tmp, "%s%d", (i > 0) ? "," : "", order[i]);\n\t\t\tstrcat(gMain.m_ListColumnOrder, tmp);\n\t\t}\n\t}\n\n\tfor (i = 0; i < gMain.GetNumEncoders() && i < MAX_ENCODERS; i++) {\n\t\tgMain.m_ListChecked[i] = s_encUnchecked[i] ? \'0\' : \'1\';\n\t\tgMain.m_ListChecked[i + 1] = \'\\000\';\n\t}\n\n\tmemset(&wp, 0, sizeof(wp));\n\twp.length = sizeof(wp);\n\tif (GetWindowPlacement(&wp)) {\n\t\tsprintf(gMain.m_WindowSize, "%d,%d", (int) (wp.rcNormalPosition.right - wp.rcNormalPosition.left), (int) (wp.rcNormalPosition.bottom - wp.rcNormalPosition.top));\n\t}\n}\n\n/* shuicast_fixes2: colw: the window can be resized; the list grows with it */\nvoid CMainWindow::InitResizeLayout() {\n\tCRect\trc;\n\tint\t\tw = 0, h = 0;\n\n\tGetClientRect(&rc);\n\ts_initCx = rc.Width();\n\ts_initCy = rc.Height();\n\tGetWindowRect(&rc);\n\ts_minWinW = rc.Width();\n\ts_minWinH = rc.Height();\n\tm_Encoders.GetWindowRect(&s_rcList);\n\tScreenToClient(&s_rcList);\n\tif (GetDlgItem(IDC_METADATA) != NULL) {\n\t\tGetDlgItem(IDC_METADATA)->GetWindowRect(&s_rcMeta);\n\t\tScreenToClient(&s_rcMeta);\n\t}\n\tif (GetDlgItem(IDC_STATIC_STATUS) != NULL) {\n\t\tGetDlgItem(IDC_STATIC_STATUS)->GetWindowRect(&s_rcStatus);\n\t\tScreenToClient(&s_rcStatus);\n\t}\n\ts_layoutReady = true;\n\n\tif (sscanf(gMain.m_WindowSize, "%d,%d", &w, &h) == 2 && w >= s_minWinW && h >= s_minWinH &&\n\t\tw <= GetSystemMetrics(SM_CXSCREEN) && h <= GetSystemMetrics(SM_CYSCREEN)) {\n\t\tSetWindowPos(NULL, 0, 0, w, h, SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE);\n\t\tCenterWindow();\n\t}\n}\n\nvoid CMainWindow::OnSize(UINT nType, int cx, int cy) {\n\tCWnd\t*pWnd;\n\tint\t\tdx, dy;\n\n\tCDialog::OnSize(nType, cx, cy);\n\tif (!s_layoutReady || nType == SIZE_MINIMIZED || m_Encoders.GetSafeHwnd() == NULL) {\n\t\treturn;\n\t}\n\tdx = cx - s_initCx;\n\tdy = cy - s_initCy;\n\tm_Encoders.SetWindowPos(NULL, s_rcList.left, s_rcList.top, (s_rcList.right - s_rcList.left) + dx, (s_rcList.bottom - s_rcList.top) + dy, SWP_NOZORDER | SWP_NOACTIVATE);\n\tpWnd = GetDlgItem(IDC_METADATA);\n\tif (pWnd != NULL) {\n\t\tpWnd->SetWindowPos(NULL, s_rcMeta.left, s_rcMeta.top, (s_rcMeta.right - s_rcMeta.left) + dx, s_rcMeta.bottom - s_rcMeta.top, SWP_NOZORDER | SWP_NOACTIVATE);\n\t}\n\tpWnd = GetDlgItem(IDC_STATIC_STATUS);\n\tif (pWnd != NULL) {\n\t\tpWnd->SetWindowPos(NULL, s_rcStatus.left, s_rcStatus.top + dy, (s_rcStatus.right - s_rcStatus.left) + dx, s_rcStatus.bottom - s_rcStatus.top, SWP_NOZORDER | SWP_NOACTIVATE);\n\t}\n}\n\nvoid CMainWindow::OnGetMinMaxInfo(MINMAXINFO *lpMMI) {\n\tCDialog::OnGetMinMaxInfo(lpMMI);\n\tif (s_layoutReady) {\n\t\tlpMMI->ptMinTrackSize.x = s_minWinW;\n\t\tlpMMI->ptMinTrackSize.y = s_minWinH;\n\t}\n}\n\n/* shuicast_fixes2: colw: the first column (with the check box) stays first; check box clicks are remembered */\nBOOL CMainWindow::OnNotify(WPARAM wParam, LPARAM lParam, LRESULT *pResult) {\n\tNMHDR\t*pHdr = (NMHDR *) lParam;\n\n\tif (pHdr != NULL) {\n\t\tif (pHdr->code == HDN_BEGINDRAG) {\n\t\t\tNMHEADER *ph = (NMHEADER *) lParam;\n\t\t\tif (ph->iItem == 0) {\n\t\t\t\t*pResult = TRUE;\n\t\t\t\treturn TRUE;\n\t\t\t}\n\t\t}\n\t\telse if (pHdr->code == HDN_ENDDRAG) {\n\t\t\tNMHEADER *ph = (NMHEADER *) lParam;\n\t\t\tif (ph->iItem == 0 || (ph->pitem != NULL && (ph->pitem->mask & HDI_ORDER) && ph->pitem->iOrder == 0)) {\n\t\t\t\t*pResult = TRUE;\n\t\t\t\treturn TRUE;\n\t\t\t}\n\t\t}\n\t\telse if (pHdr->code == LVN_ITEMCHANGED && pHdr->hwndFrom == m_Encoders.m_hWnd) {\n\t\t\tNMLISTVIEW *pnm = (NMLISTVIEW *) lParam;\n\t\t\tif (!s_syncingChecks && (pnm->uChanged & LVIF_STATE) && pnm->iItem >= 0 && pnm->iItem < MAX_ENCODERS) {\n\t\t\t\tUINT oldImg = (pnm->uOldState & LVIS_STATEIMAGEMASK) >> 12;\n\t\t\t\tUINT newImg = (pnm->uNewState & LVIS_STATEIMAGEMASK) >> 12;\n\t\t\t\tif (oldImg != 0 && newImg != 0 && oldImg != newImg) {\n\t\t\t\t\ts_encUnchecked[pnm->iItem] = (newImg != 2);\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n\treturn CDialog::OnNotify(wParam, lParam, pResult);\n}\n\n/* shuicast_fixes2: colw: fills the Mount / Stream Name columns and keeps the check boxes in step */\nvoid CMainWindow::UpdateExtraColumns() {\n\tint\t\tnumItems = m_Encoders.GetItemCount();\n\tint\t\ti;\n\n\tfor (i = 0; i < numItems && i < MAX_ENCODERS && i < gMain.GetNumEncoders(); i++) {\n\t\tint want = s_encUnchecked[i] ? 0 : 1;\n\n\t\tif (gEncoders[i] == NULL) {\n\t\t\tcontinue;\n\t\t}\n\t\t{\n\t\t\t/* the stream name is set as Unicode text so that Japanese is shown correctly */\n\t\t\tWCHAR\twnew[1100];\n\t\t\tWCHAR\twold[1100];\n\t\t\tLVITEMW\tlvi;\n\n\t\t\tMultiByteToWideChar(CP_ACP, 0, gEncoders[i]->m_ServName, -1, wnew, 1099);\n\t\t\twnew[1099] = 0;\n\t\t\twold[0] = 0;\n\t\t\tmemset(&lvi, 0, sizeof(lvi));\n\t\t\tlvi.iSubItem = 4;\n\t\t\tlvi.pszText = wold;\n\t\t\tlvi.cchTextMax = 1099;\n\t\t\t::SendMessageW(m_Encoders.m_hWnd, LVM_GETITEMTEXTW, (WPARAM) i, (LPARAM) &lvi);\n\t\t\tif (wcscmp(wold, wnew) != 0) {\n\t\t\t\tlvi.iSubItem = 4;\n\t\t\t\tlvi.pszText = wnew;\n\t\t\t\t::SendMessageW(m_Encoders.m_hWnd, LVM_SETITEMTEXTW, (WPARAM) i, (LPARAM) &lvi);\n\t\t\t}\n\t\t}\n\t\tif (m_Encoders.GetCheck(i) != want) {\n\t\t\ts_syncingChecks = true;\n\t\t\tm_Encoders.SetCheck(i, want);\n\t\t\ts_syncingChecks = false;\n\t\t}\n\t}\n}\n\n'


def fix_colw_header(t):
    if MARK_COLW in t:
        return t
    decl = (
        "void ApplyListState();   // " + MARK_COLW + "\n"
        "void SaveListState();\n"
        "void InitResizeLayout();\n"
        "void UpdateExtraColumns();\n"
        "afx_msg void OnSize(UINT nType, int cx, int cy);\n"
        "afx_msg void OnGetMinMaxInfo(MINMAXINFO *lpMMI);\n"
        "virtual BOOL OnNotify(WPARAM wParam, LPARAM lParam, LRESULT *pResult);\n"
    )
    return sub_once(
        t,
        r"([ \t]*)afx_msg void OnTimer\(UINT nIDEvent\);\n",
        lambda m: m.group(0) + "".join(m.group(1) + line + "\n" for line in decl.splitlines()),
        "MainWindow.h declarations (colw)",
    )


def fix_colw_cpp(t):
    if MARK_COLW in t:
        return t
    t = sub_once(
        t,
        r"([ \t]*)ON_WM_TIMER\(\)\n",
        lambda m: m.group(0) + m.group(1) + "ON_WM_SIZE()\n" + m.group(1) + "ON_WM_GETMINMAXINFO()\n",
        "MainWindow.cpp message map (size)",
    )
    t = sub_once(
        t,
        r"int CMainWindow::startshuicast \( int which \)\n\{\n",
        lambda m: UI3_STATICS + m.group(0),
        "MainWindow.cpp start function (statics)",
    )
    t = sub_once(
        t,
        r"(if \( !gEncoders\[i\]->IsConnected\(\))( \)\n\s*\{\n\s*gEncoders\[i\]->SetForceStop\( false \);)",
        lambda m: m.group(1) + " && IsEncoderChecked(i)" + m.group(2),
        "MainWindow.cpp connect only checked encoders",
    )
    t = sub_once(
        t,
        r"([ \t]*)m_Encoders\.SetCheck\( m_Encoders\.GetSelectionMark\(\), true \);\n",
        lambda m: m.group(1) + '// %s: the focus must not force the check box on\n' % MARK_COLW,
        "MainWindow.cpp focus check",
    )
    t = sub_once(
        t,
        r"([ \t]*)m_Encoders\.SetExtendedStyle\( LVS_EX_FULLROWSELECT \);\n",
        lambda m: '    m_Encoders.InsertColumn( 4, "Stream Name" );   // %s\n    m_Encoders.SetColumnWidth( 4, 140 );\n' % MARK_COLW + m.group(1) + 'm_Encoders.SetExtendedStyle( LVS_EX_FULLROWSELECT | LVS_EX_CHECKBOXES | LVS_EX_HEADERDRAGDROP );   // %s\n' % MARK_COLW + m.group(1) + "ApplyListState();\n" + m.group(1) + "InitResizeLayout();\n",
        "MainWindow.cpp list style",
    )
    t = sub_once(
        t,
        r"([ \t]*)gMain\.SetAutoConnect\( m_AutoConnect \);\n",
        lambda m: m.group(0) + m.group(1) + "SaveListState();\n",
        "MainWindow.cpp OnDestroy",
    )
    t = sub_once(
        t,
        r"void CMainWindow::UpdateConnectionTimes \(\)\n\{\n",
        lambda m: 'void CMainWindow::UpdateConnectionTimes ()\n{\n    UpdateExtraColumns();   // %s\n' % MARK_COLW,
        "MainWindow.cpp UpdateConnectionTimes",
    )
    t = sub_once(
        t,
        r"void CMainWindow::OnTimer \( UINT nIDEvent \)\s*\{\n",
        lambda m: UI3_MEMBERS + m.group(0),
        "MainWindow.cpp members",
    )
    return t


def fix_resize_rc(t):
    m = re.search(r"IDD_SHUICAST DIALOGEX[^\n]*\nSTYLE [^\n]*", t)
    if m is None:
        fail("rc: IDD_SHUICAST STYLE not found")
    if "WS_THICKFRAME" in m.group(0):
        return t
    end = m.end()
    if t[end - 1] == "\r":
        end -= 1
    return t[:end] + " | WS_THICKFRAME | WS_MAXIMIZEBOX" + t[end:]


# ----------------------------------- 11. config file backups (.bak1 / .bak2)
MARK_BAK = TAG + ": backup"

BACKUP_FUNC = """/*
 * %s: config file backups.
 * The new config is written to <file>.new first. When it differs from the current file, the
 * current file is kept as <file>.bak1 (the old .bak1 becomes .bak2). This is done once per
 * run and file, so many saves in one run do not push out the older backups.
 * Replacing the file from the finished .new file also protects against a half-written config.
 */
static void CommitConfigFile ( const char *configFile, const char *tmpFile )
{
    static char  backedUp[32][1100];
    static int   numBackedUp = 0;
    FILE        *fa = fopen( configFile, "rb" );
    FILE        *fb = fopen( tmpFile, "rb" );
    int          existed = ( fa != NULL );
    int          same = 0;
    int          i;

    if ( fa != NULL && fb != NULL )
    {
        same = 1;
        for ( ;; )
        {
            int ca = fgetc( fa );
            int cb = fgetc( fb );

            if ( ca != cb )
            {
                same = 0;
                break;
            }
            if ( ca == EOF )
            {
                break;
            }
        }
    }
    if ( fa != NULL ) fclose( fa );
    if ( fb != NULL ) fclose( fb );
    if ( same )
    {
        remove( tmpFile );
        return;
    }
    if ( existed )
    {
        int already = 0;

        for ( i = 0; i < numBackedUp; i++ )
        {
            if ( !strcmp( backedUp[i], configFile ) ) already = 1;
        }
        if ( !already && numBackedUp < 32 )
        {
            char bak1[1100];
            char bak2[1100];

            strcpy( backedUp[numBackedUp++], configFile );
            sprintf( bak1, "%%s.bak1", configFile );
            sprintf( bak2, "%%s.bak2", configFile );
            CopyFileA( bak1, bak2, FALSE );
            CopyFileA( configFile, bak1, FALSE );
        }
    }
    if ( !MoveFileExA( tmpFile, configFile, MOVEFILE_REPLACE_EXISTING | MOVEFILE_COPY_ALLOWED ) )
    {
        CopyFileA( tmpFile, configFile, FALSE );
        remove( tmpFile );
    }
}

"""


def fix_backup(t):
    if MARK_BAK in t:
        return t
    t = sub_once(
        t,
        r"void CEncoder::WriteConfigFile \(\)\n\{\n",
        lambda m: (BACKUP_FUNC % MARK_BAK) + m.group(0),
        "libshuicast.cpp WriteConfigFile",
    )
    t = sub_once(
        t,
        r'([ \t]*)FILE \*filep = fopen\( configFile, "w" \);\n',
        lambda m: (
            m.group(1) + "char_t tmpFile[1100] = \"\";\n"
            + m.group(1) + "sprintf( tmpFile, \"%s.new\", configFile );\n"
            + m.group(1) + "FILE *filep = fopen( tmpFile, \"w\" );\n"
        ),
        "libshuicast.cpp config fopen",
    )
    t = sub_once(
        t,
        r'([ \t]*)fclose\( filep \);\n([ \t]*\}\n[ \t]*else\n[ \t]*\{\n[ \t]*LogMessage\( LOG_ERROR, "Cannot open config file)',
        lambda m: m.group(0).replace(
            "fclose( filep );\n", "fclose( filep );\n" + m.group(1) + "CommitConfigFile( configFile, tmpFile );\n", 1
        ),
        "libshuicast.cpp config fclose",
    )
    return t


# ------------------------------ 12. TLS (HTTPS) for the server connection
MARK_TLS = TAG + ": tls"

TLSCLIENT_H = r'''/*
 * tlsclient.h - minimal TLS 1.2 client for the Win32 sockets used by this encoder,
 * built on the Windows SChannel (SSPI). No extra DLLs are needed.
 *
 *   tlsc_attach(sock, host, ...)  run the TLS handshake on a connected socket and
 *                                 verify the server certificate
 *   tlsc_send / tlsc_recv         like send() / recv(); plain sockets pass through
 *   tlsc_close(sock)              TLS shutdown (if any) and closesocket()
 *
 * Certificate check: the Windows certificate store and the host name are used.
 * A certificate that fails the check is accepted only if its SHA-256 fingerprint is
 * the pinned one, or the trust callback returns non-zero (the app asks the user and
 * then remembers the fingerprint).
 */
#ifndef TLSCLIENT_H_INCLUDED
#define TLSCLIENT_H_INCLUDED

#ifndef SECURITY_WIN32
#define SECURITY_WIN32
#endif
#include <windows.h>   /* the sockets of this program use the winsock.h API that windows.h brings in */
#include <wincrypt.h>
#include <schannel.h>
#include <security.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _MSC_VER
#pragma comment(lib, "secur32.lib")
#pragma comment(lib, "crypt32.lib")
#endif

#ifndef SCH_USE_STRONG_CRYPTO
#define SCH_USE_STRONG_CRYPTO 0x00400000
#endif
#ifndef SCH_CRED_NO_SERVERNAME_CHECK
#define SCH_CRED_NO_SERVERNAME_CHECK 0x00000004
#endif
#ifndef SP_PROT_TLS1_2_CLIENT
#define SP_PROT_TLS1_2_CLIENT 0x00000800
#endif
#ifndef CERT_SHA256_HASH_PROP_ID
#define CERT_SHA256_HASH_PROP_ID 107
#endif

#define TLSC_MAX_CONN 64
#define TLSC_INBUF_SIZE 40000

typedef int (*tlsc_trust_fn)(void *ctx, const char *fingerprint, unsigned long certError);

typedef struct tlsc_conn_s {
    SOCKET          sock;
    CredHandle      cred;
    CtxtHandle      ctx;
    int             haveCred;
    int             haveCtx;
    SecPkgContext_StreamSizes sizes;
    unsigned char  *inbuf;      /* encrypted bytes received, not yet decrypted */
    int             inlen;
    unsigned char  *plain;      /* decrypted bytes not yet handed out */
    int             plainlen;
    int             plainoff;
    unsigned char  *outbuf;
    int             outcap;
    int             refs;
    int             dead;
    CRITICAL_SECTION cs;
} tlsc_conn;

static tlsc_conn *g_tlsc_table[TLSC_MAX_CONN];
static volatile LONG g_tlsc_lock = 0;

static void tlsc_lock(void)
{
    while (InterlockedCompareExchange(&g_tlsc_lock, 1, 0) != 0) {
        Sleep(0);
    }
}

static void tlsc_unlock(void)
{
    InterlockedExchange(&g_tlsc_lock, 0);
}

static void tlsc_free_conn(tlsc_conn *c)
{
    if (c->haveCtx) {
        DeleteSecurityContext(&c->ctx);
    }
    if (c->haveCred) {
        FreeCredentialsHandle(&c->cred);
    }
    if (c->inbuf) free(c->inbuf);
    if (c->plain) free(c->plain);
    if (c->outbuf) free(c->outbuf);
    DeleteCriticalSection(&c->cs);
    free(c);
}

/* find the connection of a socket and take a reference to it */
static tlsc_conn *tlsc_acquire(SOCKET s)
{
    tlsc_conn *found = NULL;
    int i;

    tlsc_lock();
    for (i = 0; i < TLSC_MAX_CONN; i++) {
        if (g_tlsc_table[i] != NULL && g_tlsc_table[i]->sock == s && !g_tlsc_table[i]->dead) {
            found = g_tlsc_table[i];
            found->refs++;
            break;
        }
    }
    tlsc_unlock();
    return found;
}

static void tlsc_release(tlsc_conn *c)
{
    int freeit = 0;

    tlsc_lock();
    c->refs--;
    if (c->refs <= 0 && c->dead) {
        freeit = 1;
    }
    tlsc_unlock();
    if (freeit) {
        tlsc_free_conn(c);
    }
}

static int tlsc_raw_send_all(SOCKET s, const unsigned char *p, int len)
{
    int done = 0;

    while (done < len) {
        int n = send(s, (const char *) p + done, len - done, 0);
        if (n <= 0) {
            return -1;
        }
        done += n;
    }
    return done;
}

static void tlsc_hex(const unsigned char *in, int n, char *out)
{
    static const char digits[] = "0123456789ABCDEF";
    int i;

    for (i = 0; i < n; i++) {
        out[i * 2] = digits[in[i] >> 4];
        out[i * 2 + 1] = digits[in[i] & 15];
    }
    out[n * 2] = '\000';
}

static void tlsc_fingerprint(PCCERT_CONTEXT cert, char *hex65)
{
    unsigned char hash[32];
    DWORD len = sizeof(hash);

    hex65[0] = '\000';
    if (CertGetCertificateContextProperty(cert, CERT_SHA256_HASH_PROP_ID, hash, &len) && len == 32) {
        tlsc_hex(hash, 32, hex65);
    }
}

/* Windows certificate check (store + host name). Returns 0 when the certificate is trusted. */
static unsigned long tlsc_check_chain(PCCERT_CONTEXT cert, const char *host)
{
    CERT_CHAIN_PARA            cp;
    PCCERT_CHAIN_CONTEXT       chain = NULL;
    HTTPSPolicyCallbackData    https;
    CERT_CHAIN_POLICY_PARA     pp;
    CERT_CHAIN_POLICY_STATUS   ps;
    WCHAR                      whost[256];
    LPSTR                      usages[] = { (LPSTR) szOID_PKIX_KP_SERVER_AUTH, (LPSTR) szOID_SERVER_GATED_CRYPTO, (LPSTR) szOID_SGC_NETSCAPE };
    unsigned long              result;

    memset(&cp, 0, sizeof(cp));
    cp.cbSize = sizeof(cp);
    cp.RequestedUsage.dwType = USAGE_MATCH_TYPE_OR;
    cp.RequestedUsage.Usage.cUsageIdentifier = 3;
    cp.RequestedUsage.Usage.rgpszUsageIdentifier = usages;

    if (!CertGetCertificateChain(NULL, cert, NULL, cert->hCertStore, &cp, 0, NULL, &chain)) {
        return GetLastError() ? GetLastError() : 0x80090300;
    }

    whost[0] = 0;
    MultiByteToWideChar(CP_ACP, 0, host, -1, whost, 255);
    whost[255] = 0;

    memset(&https, 0, sizeof(https));
    https.cbStruct = sizeof(https);
    https.dwAuthType = AUTHTYPE_SERVER;
    https.fdwChecks = 0;
    https.pwszServerName = whost;

    memset(&pp, 0, sizeof(pp));
    pp.cbSize = sizeof(pp);
    pp.pvExtraPolicyPara = &https;

    memset(&ps, 0, sizeof(ps));
    ps.cbSize = sizeof(ps);

    if (!CertVerifyCertificateChainPolicy(CERT_CHAIN_POLICY_SSL, chain, &pp, &ps)) {
        result = GetLastError() ? GetLastError() : 0x80090300;
    }
    else {
        result = ps.dwError;
    }
    CertFreeCertificateChain(chain);
    return result;
}

/*
 * Handshake on a connected socket. Returns 1 on success (the socket is then TLS
 * wrapped), 0 on failure (err gets a short English reason; the socket is left open).
 * fpOut (at least 65 bytes) receives the server certificate fingerprint when known.
 * certErrOut receives the Windows certificate error when the certificate was not
 * trusted by the store (0 = trusted or pinned).
 */
static int tlsc_attach(SOCKET s, const char *host, const char *pinned, tlsc_trust_fn trust, void *trustCtx,
                       char *fpOut, unsigned long *certErrOut, char *err, int errlen)
{
    tlsc_conn      *c;
    SCHANNEL_CRED   scred;
    TimeStamp       ts;
    SECURITY_STATUS ss;
    DWORD           flags;
    DWORD           attrs = 0;
    int             first = 1;
    int             i;
    int             ok = 0;
    PCCERT_CONTEXT  cert = NULL;
    char            fp[80] = "";
    unsigned long   certErr = 0;

    if (fpOut) fpOut[0] = '\000';
    if (certErrOut) *certErrOut = 0;
    if (err && errlen > 0) err[0] = '\000';

    c = (tlsc_conn *) calloc(1, sizeof(tlsc_conn));
    if (c == NULL) {
        _snprintf(err, errlen, "out of memory");
        return 0;
    }
    InitializeCriticalSection(&c->cs);
    c->sock = s;
    c->refs = 1;
    c->inbuf = (unsigned char *) malloc(TLSC_INBUF_SIZE);
    c->plain = NULL;
    if (c->inbuf == NULL) {
        _snprintf(err, errlen, "out of memory");
        tlsc_free_conn(c);
        return 0;
    }

    memset(&scred, 0, sizeof(scred));
    scred.dwVersion = SCHANNEL_CRED_VERSION;
    scred.grbitEnabledProtocols = SP_PROT_TLS1_2_CLIENT;
    scred.dwFlags = SCH_CRED_NO_DEFAULT_CREDS | SCH_CRED_MANUAL_CRED_VALIDATION | SCH_USE_STRONG_CRYPTO | SCH_CRED_NO_SERVERNAME_CHECK;

    ss = AcquireCredentialsHandleA(NULL, (SEC_CHAR *) UNISP_NAME_A, SECPKG_CRED_OUTBOUND, NULL, &scred, NULL, NULL, &c->cred, &ts);
    if (ss != SEC_E_OK) {
        _snprintf(err, errlen, "AcquireCredentialsHandle failed (0x%08lX)", (unsigned long) ss);
        tlsc_free_conn(c);
        return 0;
    }
    c->haveCred = 1;

    flags = ISC_REQ_SEQUENCE_DETECT | ISC_REQ_REPLAY_DETECT | ISC_REQ_CONFIDENTIALITY | ISC_RET_EXTENDED_ERROR |
            ISC_REQ_ALLOCATE_MEMORY | ISC_REQ_STREAM | ISC_REQ_MANUAL_CRED_VALIDATION;

    for (;;) {
        SecBuffer     inb[2];
        SecBuffer     outb[1];
        SecBufferDesc ind, outd;
        int           haveInput = !first;

        if (haveInput && c->inlen == 0) {
            /* need more data from the server */
            int n = recv(s, (char *) c->inbuf, TLSC_INBUF_SIZE, 0);
            if (n <= 0) {
                _snprintf(err, errlen, "connection closed during TLS handshake");
                goto done;
            }
            c->inlen = n;
        }

        outb[0].pvBuffer = NULL;
        outb[0].BufferType = SECBUFFER_TOKEN;
        outb[0].cbBuffer = 0;
        outd.ulVersion = SECBUFFER_VERSION;
        outd.cBuffers = 1;
        outd.pBuffers = outb;

        if (haveInput) {
            inb[0].pvBuffer = c->inbuf;
            inb[0].cbBuffer = c->inlen;
            inb[0].BufferType = SECBUFFER_TOKEN;
            inb[1].pvBuffer = NULL;
            inb[1].cbBuffer = 0;
            inb[1].BufferType = SECBUFFER_EMPTY;
            ind.ulVersion = SECBUFFER_VERSION;
            ind.cBuffers = 2;
            ind.pBuffers = inb;
        }

        ss = InitializeSecurityContextA(&c->cred, first ? NULL : &c->ctx, (SEC_CHAR *) host, flags, 0, 0,
                                        haveInput ? &ind : NULL, 0, &c->ctx, &outd, &attrs, &ts);
        if (ss == SEC_E_OK || ss == SEC_I_CONTINUE_NEEDED || (FAILED(ss) && (attrs & ISC_RET_EXTENDED_ERROR))) {
            c->haveCtx = 1;
        }
        first = 0;

        if (outb[0].cbBuffer > 0 && outb[0].pvBuffer != NULL) {
            int sent = tlsc_raw_send_all(s, (const unsigned char *) outb[0].pvBuffer, (int) outb[0].cbBuffer);
            FreeContextBuffer(outb[0].pvBuffer);
            if (sent < 0) {
                _snprintf(err, errlen, "send failed during TLS handshake");
                goto done;
            }
        }

        if (ss == SEC_E_INCOMPLETE_MESSAGE) {
            int n;
            if (c->inlen >= TLSC_INBUF_SIZE) {
                _snprintf(err, errlen, "TLS handshake message too large");
                goto done;
            }
            n = recv(s, (char *) c->inbuf + c->inlen, TLSC_INBUF_SIZE - c->inlen, 0);
            if (n <= 0) {
                _snprintf(err, errlen, "connection closed during TLS handshake");
                goto done;
            }
            c->inlen += n;
            first = 0;
            continue;
        }

        if (ss == SEC_E_OK || ss == SEC_I_CONTINUE_NEEDED) {
            /* keep bytes that were not part of the handshake */
            if (haveInput) {
                if (inb[1].BufferType == SECBUFFER_EXTRA && inb[1].cbBuffer > 0) {
                    memmove(c->inbuf, c->inbuf + (c->inlen - inb[1].cbBuffer), inb[1].cbBuffer);
                    c->inlen = (int) inb[1].cbBuffer;
                }
                else {
                    c->inlen = 0;
                }
            }
            if (ss == SEC_E_OK) {
                break;
            }
            continue;
        }

        if (ss == SEC_I_INCOMPLETE_CREDENTIALS) {
            _snprintf(err, errlen, "server asks for a client certificate (not supported)");
            goto done;
        }

        _snprintf(err, errlen, "TLS handshake failed (0x%08lX)", (unsigned long) ss);
        goto done;
    }

    if (QueryContextAttributes(&c->ctx, SECPKG_ATTR_STREAM_SIZES, &c->sizes) != SEC_E_OK) {
        _snprintf(err, errlen, "cannot get the TLS record sizes");
        goto done;
    }

    /* certificate check */
    if (QueryContextAttributes(&c->ctx, SECPKG_ATTR_REMOTE_CERT_CONTEXT, &cert) != SEC_E_OK || cert == NULL) {
        _snprintf(err, errlen, "the server sent no certificate");
        goto done;
    }
    tlsc_fingerprint(cert, fp);
    if (fpOut) {
        strncpy(fpOut, fp, 64);
        fpOut[64] = '\000';
    }
    if (pinned != NULL && pinned[0] != '\000' && fp[0] != '\000' && _stricmp(pinned, fp) == 0) {
        certErr = 0;   /* the pinned (trusted) certificate */
    }
    else {
        certErr = tlsc_check_chain(cert, host);
        if (certErr != 0) {
            if (certErrOut) *certErrOut = certErr;
            if (trust != NULL && fp[0] != '\000' && trust(trustCtx, fp, certErr)) {
                certErr = 0;
            }
        }
    }
    if (certErr != 0) {
        _snprintf(err, errlen, "server certificate not trusted (0x%08lX)", certErr);
        goto done;
    }

    c->outcap = (int) (c->sizes.cbHeader + c->sizes.cbMaximumMessage + c->sizes.cbTrailer);
    c->outbuf = (unsigned char *) malloc(c->outcap);
    c->plain = (unsigned char *) malloc(c->sizes.cbMaximumMessage > 0 ? c->sizes.cbMaximumMessage : 16384);
    if (c->outbuf == NULL || c->plain == NULL) {
        _snprintf(err, errlen, "out of memory");
        goto done;
    }

    tlsc_lock();
    for (i = 0; i < TLSC_MAX_CONN; i++) {
        if (g_tlsc_table[i] == NULL) {
            g_tlsc_table[i] = c;
            ok = 1;
            break;
        }
    }
    tlsc_unlock();
    if (!ok) {
        _snprintf(err, errlen, "too many TLS connections");
    }

done:
    if (cert != NULL) {
        CertFreeCertificateContext(cert);
    }
    if (!ok) {
        tlsc_free_conn(c);
    }
    return ok;
}

/* send(): the data is encrypted in records. Plain sockets pass through. Returns len or -1. */
static int tlsc_send(SOCKET s, const char *data, int len, int flags)
{
    tlsc_conn *c = tlsc_acquire(s);
    int        done = 0;

    if (c == NULL) {
        return send(s, data, len, flags);
    }
    EnterCriticalSection(&c->cs);
    while (done < len && !c->dead) {
        SecBuffer     b[4];
        SecBufferDesc d;
        int           chunk = len - done;
        int           total;

        if (chunk > (int) c->sizes.cbMaximumMessage) {
            chunk = (int) c->sizes.cbMaximumMessage;
        }
        memcpy(c->outbuf + c->sizes.cbHeader, data + done, chunk);
        b[0].pvBuffer = c->outbuf;
        b[0].cbBuffer = c->sizes.cbHeader;
        b[0].BufferType = SECBUFFER_STREAM_HEADER;
        b[1].pvBuffer = c->outbuf + c->sizes.cbHeader;
        b[1].cbBuffer = chunk;
        b[1].BufferType = SECBUFFER_DATA;
        b[2].pvBuffer = c->outbuf + c->sizes.cbHeader + chunk;
        b[2].cbBuffer = c->sizes.cbTrailer;
        b[2].BufferType = SECBUFFER_STREAM_TRAILER;
        b[3].pvBuffer = NULL;
        b[3].cbBuffer = 0;
        b[3].BufferType = SECBUFFER_EMPTY;
        d.ulVersion = SECBUFFER_VERSION;
        d.cBuffers = 4;
        d.pBuffers = b;
        if (EncryptMessage(&c->ctx, 0, &d, 0) != SEC_E_OK) {
            done = -1;
            break;
        }
        total = (int) (b[0].cbBuffer + b[1].cbBuffer + b[2].cbBuffer);
        if (tlsc_raw_send_all(s, c->outbuf, total) < 0) {
            done = -1;
            break;
        }
        done += chunk;
    }
    if (c->dead && done < len) {
        done = -1;
    }
    LeaveCriticalSection(&c->cs);
    tlsc_release(c);
    return done;
}

/* recv(): returns the number of decrypted bytes, 0 when the server closed, -1 on error. */
static int tlsc_recv(SOCKET s, char *buf, int len, int flags)
{
    tlsc_conn *c = tlsc_acquire(s);
    int        result = -1;

    if (c == NULL) {
        return recv(s, buf, len, flags);
    }
    EnterCriticalSection(&c->cs);
    for (;;) {
        SecBuffer     b[4];
        SecBufferDesc d;
        SECURITY_STATUS ss;
        int           i;

        if (c->dead) {
            result = -1;
            break;
        }
        if (c->plainoff < c->plainlen) {
            int n = c->plainlen - c->plainoff;
            if (n > len) n = len;
            memcpy(buf, c->plain + c->plainoff, n);
            c->plainoff += n;
            result = n;
            break;
        }
        if (c->inlen > 0) {
            b[0].pvBuffer = c->inbuf;
            b[0].cbBuffer = c->inlen;
            b[0].BufferType = SECBUFFER_DATA;
            for (i = 1; i < 4; i++) {
                b[i].pvBuffer = NULL;
                b[i].cbBuffer = 0;
                b[i].BufferType = SECBUFFER_EMPTY;
            }
            d.ulVersion = SECBUFFER_VERSION;
            d.cBuffers = 4;
            d.pBuffers = b;
            ss = DecryptMessage(&c->ctx, &d, 0, NULL);
            if (ss == SEC_E_OK) {
                unsigned char *dataPtr = NULL;
                int            dataLen = 0;
                int            extraLen = 0;

                for (i = 1; i < 4; i++) {
                    if (b[i].BufferType == SECBUFFER_DATA && dataPtr == NULL) {
                        dataPtr = (unsigned char *) b[i].pvBuffer;
                        dataLen = (int) b[i].cbBuffer;
                    }
                    if (b[i].BufferType == SECBUFFER_EXTRA) {
                        extraLen = (int) b[i].cbBuffer;
                    }
                }
                if (dataPtr != NULL && dataLen > 0) {
                    if (dataLen > (int) c->sizes.cbMaximumMessage) {
                        dataLen = (int) c->sizes.cbMaximumMessage;
                    }
                    memcpy(c->plain, dataPtr, dataLen);
                    c->plainlen = dataLen;
                    c->plainoff = 0;
                }
                if (extraLen > 0) {
                    memmove(c->inbuf, c->inbuf + (c->inlen - extraLen), extraLen);
                    c->inlen = extraLen;
                }
                else {
                    c->inlen = 0;
                }
                continue;
            }
            if (ss == SEC_I_CONTEXT_EXPIRED) {
                result = 0;
                break;
            }
            if (ss != SEC_E_INCOMPLETE_MESSAGE) {
                result = -1;
                break;
            }
        }
        if (c->inlen >= TLSC_INBUF_SIZE) {
            result = -1;
            break;
        }
        {
            int n = recv(s, (char *) c->inbuf + c->inlen, TLSC_INBUF_SIZE - c->inlen, 0);
            if (n <= 0) {
                result = n;
                break;
            }
            c->inlen += n;
        }
    }
    LeaveCriticalSection(&c->cs);
    tlsc_release(c);
    return result;
}

/* TLS shutdown (when the socket is TLS wrapped) and closesocket() */
static int tlsc_close(SOCKET s)
{
    tlsc_conn *c = NULL;
    int        i;

    tlsc_lock();
    for (i = 0; i < TLSC_MAX_CONN; i++) {
        if (g_tlsc_table[i] != NULL && g_tlsc_table[i]->sock == s) {
            c = g_tlsc_table[i];
            g_tlsc_table[i] = NULL;
            c->dead = 1;
            c->refs++;
            break;
        }
    }
    tlsc_unlock();

    if (c != NULL) {
        if (TryEnterCriticalSection(&c->cs)) {
            /* best effort close_notify */
            DWORD         type = SCHANNEL_SHUTDOWN;
            SecBuffer     ob;
            SecBufferDesc od;
            DWORD         attrs;
            TimeStamp     ts;
            SecBuffer     tb;

            tb.pvBuffer = &type;
            tb.cbBuffer = sizeof(type);
            tb.BufferType = SECBUFFER_TOKEN;
            od.ulVersion = SECBUFFER_VERSION;
            od.cBuffers = 1;
            od.pBuffers = &tb;
            if (ApplyControlToken(&c->ctx, &od) == SEC_E_OK) {
                ob.pvBuffer = NULL;
                ob.cbBuffer = 0;
                ob.BufferType = SECBUFFER_TOKEN;
                od.pBuffers = &ob;
                if (InitializeSecurityContextA(&c->cred, &c->ctx, NULL,
                                               ISC_REQ_SEQUENCE_DETECT | ISC_REQ_REPLAY_DETECT | ISC_REQ_CONFIDENTIALITY |
                                               ISC_RET_EXTENDED_ERROR | ISC_REQ_ALLOCATE_MEMORY | ISC_REQ_STREAM,
                                               0, 0, NULL, 0, &c->ctx, &od, &attrs, &ts) == SEC_E_OK) {
                    if (ob.pvBuffer != NULL && ob.cbBuffer > 0) {
                        tlsc_raw_send_all(s, (const unsigned char *) ob.pvBuffer, (int) ob.cbBuffer);
                    }
                    if (ob.pvBuffer != NULL) {
                        FreeContextBuffer(ob.pvBuffer);
                    }
                }
            }
            LeaveCriticalSection(&c->cs);
        }
        else {
            shutdown(s, 2);   /* wake up a call that is still running */
        }
        tlsc_release(c);
    }
    return closesocket(s);
}

#endif /* TLSCLIENT_H_INCLUDED */
'''

TLS_CPP = '/* shuicast_fixes2: tls: TLS for the server connection (Windows SChannel, see tlsclient.h) */\nstatic const WCHAR *acTlsReason(unsigned long e) {\n\tswitch (e) {\n\t\tcase 0x800B0109UL:\n\t\tcase 0x800B0112UL:\n\t\t\treturn L"\\u81ea\\u5df1\\u7f72\\u540d\\u3001\\u307e\\u305f\\u306f\\u4fe1\\u983c\\u3055\\u308c\\u3066\\u3044\\u306a\\u3044\\u767a\\u884c\\u5143\\u306e\\u8a3c\\u660e\\u66f8\\u3067\\u3059";\n\t\tcase 0x800B010FUL:\n\t\t\treturn L"\\u30db\\u30b9\\u30c8\\u540d\\u304c\\u8a3c\\u660e\\u66f8\\u3068\\u4e00\\u81f4\\u3057\\u307e\\u305b\\u3093";\n\t\tcase 0x800B0101UL:\n\t\t\treturn L"\\u8a3c\\u660e\\u66f8\\u306e\\u671f\\u9650\\u304c\\u5207\\u308c\\u3066\\u3044\\u307e\\u3059";\n\t}\n\treturn L"\\u8a3c\\u660e\\u66f8\\u3092\\u691c\\u8a3c\\u3067\\u304d\\u307e\\u305b\\u3093";\n}\n\nstatic int acTlsTrust(void *ctx, const char *fp, unsigned long certErr) {\n\tCEncoder\t*g = (CEncoder *) ctx;\n\tWCHAR\tmsg[1500];\n\tWCHAR\twfp[100];\n\tWCHAR\twhost[300];\n\tint\t\tanswer;\n\n\tif (g->m_TLSPinned[0] != \'\\000\') {\n\t\tg->LogMessage( LOG_ERROR, "TLS: the server certificate (SHA-256 %s) is not the trusted one. To trust a new certificate, delete ServerTLSTrustedCert in the config file.", fp);\n\t\treturn 0;\n\t}\n\tif (strcmp(g->m_TLSDeclined, fp) == 0) {\n\t\treturn 0;\n\t}\n\tMultiByteToWideChar(CP_ACP, 0, fp, -1, wfp, 99);\n\twfp[99] = 0;\n\tMultiByteToWideChar(CP_ACP, 0, g->m_Server, -1, whost, 299);\n\twhost[299] = 0;\n\t_snwprintf(msg, 1499, L"TLS\\u3067\\u63a5\\u7d9a\\u3059\\u308b\\u30b5\\u30fc\\u30d0\\u30fc\\u306e\\u8a3c\\u660e\\u66f8\\u3092\\u78ba\\u8a8d\\u3067\\u304d\\u307e\\u305b\\u3093\\u3067\\u3057\\u305f\\u3002\\n\\n\\u30b5\\u30fc\\u30d0\\u30fc: %ls\\n\\u7406\\u7531: %ls\\nSHA-256\\u6307\\u7d0b:\\n%ls\\n\\n\\u3053\\u306e\\u8a3c\\u660e\\u66f8\\u3092\\u4fe1\\u983c\\u3057\\u3066\\u63a5\\u7d9a\\u3057\\u3001\\u4ee5\\u5f8c\\u3053\\u306e\\u8a3c\\u660e\\u66f8\\u3060\\u3051\\u3092\\u8a31\\u53ef\\u3057\\u307e\\u3059\\u304b\\uff1f\\n\\u5225\\u306e\\u30b5\\u30fc\\u30d0\\u30fc\\u306b\\u3088\\u308b\\u507d\\u306e\\u8a3c\\u660e\\u66f8\\u306e\\u53ef\\u80fd\\u6027\\u3082\\u3042\\u308a\\u307e\\u3059\\u3002\\u6307\\u7d0b\\u304c\\u6b63\\u3057\\u3044\\u3068\\u78ba\\u8a8d\\u3067\\u304d\\u308b\\u5834\\u5408\\u306e\\u307f\\u300c\\u306f\\u3044\\u300d\\u3092\\u9078\\u3093\\u3067\\u304f\\u3060\\u3055\\u3044\\u3002", whost, acTlsReason(certErr), wfp);\n\tmsg[1499] = 0;\n\tanswer = MessageBoxW(NULL, msg, L"\\u30b5\\u30fc\\u30d0\\u30fc\\u8a3c\\u660e\\u66f8\\u306e\\u78ba\\u8a8d", MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_TOPMOST | MB_SETFOREGROUND);\n\tif (answer == IDYES) {\n\t\tstrncpy(g->m_TLSPinned, fp, sizeof(g->m_TLSPinned) - 1);\n\t\tg->m_TLSPinned[sizeof(g->m_TLSPinned) - 1] = \'\\000\';\n\t\tg->WriteConfigFile();\n\t\treturn 1;\n\t}\n\tstrncpy(g->m_TLSDeclined, fp, sizeof(g->m_TLSDeclined) - 1);\n\tg->m_TLSDeclined[sizeof(g->m_TLSDeclined) - 1] = \'\\000\';\n\treturn 0;\n}\n\nstatic int acTlsAttach(CEncoder *g, SOCKET s, int report) {\n\tchar\t\t\tfp[80] = "";\n\tchar\t\t\terr[256] = "";\n\tunsigned long\tcertErr = 0;\n\n\tif (tlsc_attach(s, g->m_Server, g->m_TLSPinned, acTlsTrust, g, fp, &certErr, err, sizeof(err))) {\n\t\treturn 1;\n\t}\n\tg->LogMessage( LOG_ERROR, "TLS: %s", err);\n\tif (report) {\n\t\tg->SetServerStatus( "TLS failed (see log)" );\n\t}\n\treturn 0;\n}\n\n'


def write_tlsclient(rel):
    path = SRC / rel
    if not path.parent.exists():
        fail("%s: folder not found" % rel)
    data = TLSCLIENT_H.encode("latin-1")
    if path.exists() and path.read_bytes() == data:
        return
    path.write_bytes(data)
    print(TAG + ": wrote " + rel)


def fix_tls_resource(t):
    if "IDC_USE_TLS" in t:
        return t
    return sub_once(
        t,
        r"(\n)(#ifdef APSTUDIO_INVOKED)",
        lambda m: m.group(1) + "#define IDC_USE_TLS                     1190\n\n" + m.group(2),
        "resource.h",
    )


def fix_tls_rc(t):
    if "IDC_USE_TLS" in t:
        return t
    return sub_once(
        t,
        r"([ \t]*EDITTEXT[ \t]+IDC_SERVER_PORT,[^\n]*\n)",
        lambda m: m.group(1) + '    CONTROL         "Use TLS (HTTPS)",IDC_USE_TLS,"Button",BS_AUTOCHECKBOX | WS_TABSTOP,160,133,110,10\n',
        "rc server port",
    )


def fix_tls_bs_header(t):
    if "m_UseTLS" in t:
        return t
    return sub_once(
        t,
        r"([ \t]*)BOOL[ \t]+m_UseBitrate;\n",
        lambda m: m.group(0) + m.group(1) + "BOOL      m_UseTLS;\n",
        "BasicSettings.h",
    )


def fix_tls_bs_cpp(t):
    if "m_UseTLS" in t:
        return t
    t = sub_once(
        t,
        r"([ \t]*)m_UseBitrate = FALSE;\n",
        lambda m: m.group(0) + m.group(1) + "m_UseTLS = FALSE;\n",
        "BasicSettings.cpp ctor",
    )
    t = sub_once(
        t,
        r"([ \t]*)DDX_Check\(pDX, IDC_USEBITRATE, m_UseBitrate\);\n",
        lambda m: m.group(0) + m.group(1) + "DDX_Check(pDX, IDC_USE_TLS, m_UseTLS);\n",
        "BasicSettings.cpp DDX",
    )
    return t


def fix_tls_config(t):
    if "m_UseTLS" in t:
        return t
    t = sub_once(
        t,
        r"([ \t]*)basicSettings->m_ServerPort = encoder->m_Port;\n",
        lambda m: m.group(0) + m.group(1) + "basicSettings->m_UseTLS = encoder->m_UseTLS ? TRUE : FALSE;\n",
        "Config.cpp globals to dialog",
    )
    t = sub_once(
        t,
        r"([ \t]*)strcpy\( encoder->m_Port,[ \t]*LPCSTR\( basicSettings->m_ServerPort \) \);\n",
        lambda m: m.group(0) + m.group(1) + "encoder->m_UseTLS = basicSettings->m_UseTLS ? 1 : 0;\n",
        "Config.cpp dialog to globals",
    )
    return t


def _tls_members(m):
    # the members are public; the access of the code that follows is restored
    labels = re.findall(r"^[ \t]*(public|private|protected):", m.string[: m.start()], re.M)
    prev = labels[-1] if labels else "private"
    ind = m.group(1)
    return (
        ind + "public:   // " + MARK_TLS + "\n"
        + ind + "int                   m_UseTLS                  = 0;\n"
        + ind + 'char                  m_TLSPinned[80]           = "";\n'
        + ind + 'char                  m_TLSDeclined[80]         = "";\n'
        + ind + prev + ":\n"
    )


def fix_tls_libheader(t):
    if MARK_TLS in t:
        return t
    return sub_once(
        t,
        r"([ \t]*)int[ \t]+m_SkipCloseWarning[ \t]*= 0;\n",
        lambda m: m.group(0) + _tls_members(m),
        "libheader TLS members",
    )


def fix_tls_lib(t):
    if MARK_TLS in t:
        return t
    t = sub_once(
        t,
        r"#include \"libshuicast_socket.h\"\n",
        lambda m: m.group(0) + '#include "tlsclient.h"   // ' + MARK_TLS + '\n',
        "lib include",
    )
    t = sub_once(
        t,
        r"// this needs to be virtualized to support NSV[^\n]*\n",
        lambda m: TLS_CPP + m.group(0),
        "lib TLS block",
    )
    t, n = re.subn(r"ret = send\( sd, data, length, sendflags \);", "ret = tlsc_send( sd, data, length, sendflags );", t)
    if n != 1:
        fail("lib send: expected 1, found %d" % n)
    t, n = re.subn(r"\brecv\( m_SCSocket,", "tlsc_recv( m_SCSocket,", t)
    if n != 2:
        fail("lib recv: expected 2, found %d" % n)
    t, n = re.subn(r"closesocket\( m_SCSocket(Ctrl)? \)", lambda m: "tlsc_close( m_SCSocket" + (m.group(1) or "") + " )", t)
    if n != 5:
        fail("lib closesocket: expected 5, found %d" % n)
    t = sub_once(
        t,
        r"send\( m_SCSocketCtrl, contentString, strlen\( contentString \), 0 \);",
        lambda m: "if ( !m_UseTLS || acTlsAttach( this, m_SCSocketCtrl, 0 ) )\n                    {\n                        tlsc_send( m_SCSocketCtrl, contentString, strlen( contentString ), 0 );\n                    }",
        "lib control socket send",
    )
    t = sub_once(
        t,
        r"[ \t]*SetServerStatus\( \"Socket connected\" \);\n",
        lambda m: m.group(0) + "    // " + MARK_TLS + "\n    if ( m_UseTLS && !acTlsAttach( this, m_SCSocket, 1 ) )\n    {\n        tlsc_close( m_SCSocket );\n        return 0;\n    }\n",
        "lib connect",
    )
    t = sub_once(
        t,
        r"([ \t]*)GetConfigVariable\( gAppName, \"Port\", \"8000\", m_Port, sizeof\( m_Port \), NULL \);\n",
        lambda m: m.group(0) + "    m_UseTLS = (int) GetConfigVariable( gAppName, \"ServerTLS\", 0L, (char_t *) NULL );\n    GetConfigVariable( gAppName, \"ServerTLSTrustedCert\", \"\", m_TLSPinned, sizeof( m_TLSPinned ), NULL );\n",
        "lib config load",
    )
    t = sub_once(
        t,
        r"([ \t]*)PutConfigVariable\( gAppName, \"Port\", m_Port \);\n",
        lambda m: m.group(0) + "    PutConfigVariable( gAppName, \"ServerTLS\", (long) m_UseTLS );\n    PutConfigVariable( gAppName, \"ServerTLSTrustedCert\", m_TLSPinned );\n",
        "lib config store",
    )
    t = sub_once(
        t,
        r"([ \t]*)AddConfigVariable\( \"Port\" \);\n",
        lambda m: m.group(0) + "    AddConfigVariable( \"ServerTLS\" );\n    AddConfigVariable( \"ServerTLSTrustedCert\" );\n",
        "lib config add",
    )
    return t


patch_file("shuicast/Config.cpp", lambda t: fix_tls_config((lambda t: fix_avail_config(fix_lock_config(t)))(t)))
patch_file("shuicast/BasicSettings.h", lambda t: fix_tls_bs_header((lambda t: fix_high_header(fix_avail_header(fix_lock_header(t))))(t)))
patch_file("shuicast/BasicSettings.cpp", lambda t: fix_tls_bs_cpp((lambda t: fix_high_cpp(fix_dropdown_width(fix_selendok_refresh(fix_avail_cpp(fix_lock_cpp(t))))))(t)))
patch_file("shuicast/shuicast_standalone.cpp", fix_instance)
patch_file("libshuicast/libshuicast.cpp", lambda t: fix_tls_lib((lambda t: fix_backup(fix_colw_lib(fix_lc_label(fix_loaddll(fix_aacp(t))))))(t)))
patch_file("shuicast/shuicast.rc", lambda t: fix_tls_rc((lambda t: fix_resize_rc(fix_para_label(fix_unsorted_rc(t))))(t)))
patch_file("shuicast/MainWindow.h", lambda t: fix_colw_header(fix_time_header(t)))
patch_file("shuicast/MainWindow.cpp", lambda t: fix_colw_cpp(fix_time_cpp(t)))
patch_file("libshuicast/libshuicast.h", lambda t: fix_tls_libheader((fix_colw_libheader)(t)))
patch_file("shuicast/resource.h", fix_tls_resource)
write_tlsclient("libshuicast/tlsclient.h")
print(TAG + ": done")
