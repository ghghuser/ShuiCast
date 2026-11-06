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


patch_file("shuicast/Config.cpp", lambda t: fix_avail_config(fix_lock_config(t)))
patch_file("shuicast/BasicSettings.h", lambda t: fix_high_header(fix_avail_header(fix_lock_header(t))))
patch_file("shuicast/BasicSettings.cpp", lambda t: fix_high_cpp(fix_dropdown_width(fix_selendok_refresh(fix_avail_cpp(fix_lock_cpp(t))))))
patch_file("shuicast/shuicast_standalone.cpp", fix_instance)
patch_file("libshuicast/libshuicast.cpp", lambda t: fix_lc_label(fix_loaddll(fix_aacp(t))))
patch_file("shuicast/shuicast.rc", fix_unsorted_rc)
print(TAG + ": done")
