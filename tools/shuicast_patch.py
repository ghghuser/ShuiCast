#!/usr/bin/env python3
"""
ShuiCast patches (applied at build time):

 1. Netladio (ねとらじ) support
      * YP Settings gets two new fields: "DJ Name" and "Charset" (none / sjis / utf8)
      * both are stored in the encoder config file (ServerLadioDJ / ServerLadioCharset)
      * on the Icecast2 (ICE/1.0) SOURCE connection an extra header is sent:
            x-ladio-info: charset=sjis;dj=<URL-encoded DJ name>
        When charset is sjis/utf8 the stream name / URL / genre / description
        headers are converted to that character set too, so they match what
        x-ladio-info announces.

 2. Widens the clipped "Stream Name / Description / Genre" labels in YP Settings
      (they show only "Stream" with the Segoe UI dialog font).

 3. Recording file name   "[YYYYMMDD] <Stream Name>_hhmmss.<ext>"
      (the old name was "<Stream Description>_<asctime>").
      Characters that are illegal in file names are removed in a multibyte-safe
      way, so Shift_JIS characters whose 2nd byte is 0x5C (表, 能, ...) survive.

Usage:  python shuicast_patch.py <path to the "trunk" folder>

Idempotent (a file that already contains "ladio" is skipped) and fails loudly if
an anchor cannot be found, so a silent half-patch cannot happen.
"""
import re
import sys
from pathlib import Path

TRUNK = Path(sys.argv[1] if len(sys.argv) > 1 else "trunk")
APP = TRUNK / "shuicast"
LIB = TRUNK / "libshuicast"


def fail(msg):
    print("shuicast_patch: ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


def edit(text, pattern, insert, where, label, flags=0):
    found = list(re.finditer(pattern, text, flags))
    if len(found) != 1:
        fail("%s: expected exactly 1 match, found %d" % (label, len(found)))
    m = found[0]
    if where == "after":
        return text[: m.end()] + insert + text[m.end():]
    if where == "before":
        return text[: m.start()] + insert + text[m.start():]
    if where == "replace":
        return text[: m.start()] + insert + text[m.end():]
    fail("bad mode " + where)


def patch_file(path, func):
    if not path.exists():
        fail("%s not found" % path)
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("latin-1").replace("\r\n", "\n")
    if "ladio" in text.lower():
        print("shuicast_patch: %s already patched, skipped" % path.name)
        return
    text = func(text)
    if crlf:
        text = text.replace("\n", "\r\n")
    path.write_bytes(text.encode("latin-1"))
    print("shuicast_patch: patched %s" % path.name)


# ---------------------------------------------------------------- resource.h
def p_resource_h(t):
    return edit(
        t,
        r"#define IDC_LIVEMIX[^\n]*\n",
        "#define IDC_LADIODJ                     1106\n"
        "#define IDC_LADIOCHARSET                1107\n",
        "after",
        "resource.h IDC_LIVEMIX",
    )


# ----------------------------------------------------------------- shuicast.rc
def p_rc(t):
    # The stock labels are too narrow for the "Segoe UI" font and wrap, so only the
    # first word ("Stream") is visible. Give them more room.
    for text, y in (("Stream Name", 38), ("Stream Description", 59), ("Stream Genre", 101)):
        t = edit(
            t,
            r'"%s",IDC_STATIC,\d+,%d,\d+,8' % (text, y),
            '"%s",IDC_STATIC,10,%d,77,8' % (text, y),
            "replace",
            "shuicast.rc label " + text,
        )
    return edit(
        t,
        r"    EDITTEXT\s+IDC_IRC,[^\n]*\n",
        '    RTEXT           "DJ Name",IDC_STATIC,43,182,44,8\n'
        "    EDITTEXT        IDC_LADIODJ,93,179,157,14,ES_AUTOHSCROLL\n"
        '    RTEXT           "Charset",IDC_STATIC,43,203,44,8\n'
        "    COMBOBOX        IDC_LADIOCHARSET,93,200,70,60,"
        "CBS_DROPDOWNLIST | WS_VSCROLL | WS_TABSTOP\n",
        "after",
        "shuicast.rc IDC_IRC",
    )


# ---------------------------------------------------------------- YPSettings.h
def p_yp_h(t):
    return edit(
        t,
        r"\tCString\tm_StreamAIM;\n",
        "\tCString\tm_LadioDJ;\n"
        "\tint\t\tm_LadioCharset;\t// 0 = none, 1 = sjis, 2 = utf8\n"
        "\tCComboBox\tm_LadioCharsetCtrl;\n",
        "after",
        "YPSettings.h m_StreamAIM",
    )


# -------------------------------------------------------------- YPSettings.cpp
def p_yp_cpp(t):
    t = edit(
        t,
        r"\tm_StreamAIM = _T\(\"\"\);\n",
        '\tm_LadioDJ = _T("");\n\tm_LadioCharset = 0;\n',
        "after",
        "YPSettings.cpp ctor",
    )
    t = edit(
        t,
        r"\tDDX_Text\(pDX, IDC_AIM, m_StreamAIM\);\n",
        "\tDDX_Control(pDX, IDC_LADIOCHARSET, m_LadioCharsetCtrl);\n"
        "\tDDX_Text(pDX, IDC_LADIODJ, m_LadioDJ);\n"
        "\tDDX_CBIndex(pDX, IDC_LADIOCHARSET, m_LadioCharset);\n",
        "after",
        "YPSettings.cpp DDX",
    )
    t = edit(
        t,
        r"BOOL CYPSettings::OnInitDialog\(\)\s*\{\s*CDialog::OnInitDialog\(\);\n",
        "\n"
        "\t// Netladio (x-ladio-info) fields\n"
        "\tm_LadioCharsetCtrl.ResetContent();\n"
        '\tm_LadioCharsetCtrl.AddString("(none)");\n'
        '\tm_LadioCharsetCtrl.AddString("sjis");\n'
        '\tm_LadioCharsetCtrl.AddString("utf8");\n'
        "\tif (m_LadioCharset < 0 || m_LadioCharset > 2) {\n"
        "\t\tm_LadioCharset = 0;\n"
        "\t}\n"
        "\tm_LadioCharsetCtrl.SetCurSel(m_LadioCharset);\n"
        "\tCWnd *pDJ = GetDlgItem(IDC_LADIODJ);\n"
        "\tif (pDJ) {\n"
        "\t\tpDJ->SendMessage(EM_LIMITTEXT, 250, 0);\n"
        "\t}\n",
        "after",
        "YPSettings.cpp OnInitDialog",
    )
    return t


# ------------------------------------------------------------------- Config.cpp
def p_config_cpp(t):
    t = edit(
        t,
        r"    ypSettings->m_StreamIRC\s*=\s*encoder->m_ServIRC;\n",
        "    ypSettings->m_LadioDJ = encoder->m_LadioDJ;\n"
        '    if ( !strcmp( encoder->m_LadioCharset, "sjis" ) ) ypSettings->m_LadioCharset = 1;\n'
        '    else if ( !strcmp( encoder->m_LadioCharset, "utf8" ) ) ypSettings->m_LadioCharset = 2;\n'
        "    else ypSettings->m_LadioCharset = 0;\n",
        "after",
        "Config.cpp dialog <- encoder",
    )
    t = edit(
        t,
        r"    strcpy\( encoder->m_ServIRC,\s*LPCSTR\( ypSettings->m_StreamIRC\s*\) \);\n",
        "    strncpy( encoder->m_LadioDJ, LPCSTR( ypSettings->m_LadioDJ ), sizeof( encoder->m_LadioDJ ) - 1 );\n"
        "    encoder->m_LadioDJ[sizeof( encoder->m_LadioDJ ) - 1] = '\\000';\n"
        '    if ( ypSettings->m_LadioCharset == 1 ) strcpy( encoder->m_LadioCharset, "sjis" );\n'
        '    else if ( ypSettings->m_LadioCharset == 2 ) strcpy( encoder->m_LadioCharset, "utf8" );\n'
        "    else encoder->m_LadioCharset[0] = '\\000';\n",
        "after",
        "Config.cpp encoder <- dialog",
    )
    return t


# ------------------------------------------------------------------ libshuicast.h
def p_lib_h(t):
    return edit(
        t,
        r"    char_t\s+m_ServGenre\[100\]\s*=\s*\{\};\n",
        "    char_t                m_LadioDJ[256]            = {};  // Netladio DJ name (x-ladio-info)\n"
        '    char_t                m_LadioCharset[10]        = {};  // "", "sjis" or "utf8"\n',
        "after",
        "libshuicast.h m_ServGenre",
    )


LADIO_HELPERS = r'''/*
 * Netladio (x-ladio-info) support
 *
 * x-ladio-info: charset=<sjis|utf8>;dj=<URL-encoded DJ name>
 */

// Code page for the configured charset; 0 = leave strings untouched
static int ladioCodePage ( const char *charset )
{
	if ( !strcmp( charset, "utf8" ) ) return 65001;	// CP_UTF8
	if ( !strcmp( charset, "sjis" ) ) return 932;	// Shift_JIS
	return 0;
}

// Convert from the local (ANSI) code page to codePage. Falls back to a plain copy.
static void ladioConvert ( const char *src, int codePage, char *dst, int dstSize )
{
	dst[0] = '\000';
	if ( codePage != 0 && src[0] != '\000' )
	{
		wchar_t	wbuf[2048];
		int		wlen = MultiByteToWideChar( CP_ACP, 0, src, -1, wbuf, 2048 );

		if ( wlen > 0 )
		{
			int	blen = WideCharToMultiByte( (UINT)codePage, 0, wbuf, -1, dst, dstSize, NULL, NULL );

			if ( blen > 0 ) return;
		}
	}
	strncpy( dst, src, dstSize - 1 );
	dst[dstSize - 1] = '\000';
}

static void ladioUrlEncode ( const char *src, char *dst, int dstSize )
{
	static const char	hex[] = "0123456789ABCDEF";
	int					o = 0;

	for ( ; *src && o + 4 < dstSize; src++ )
	{
		unsigned char	c = (unsigned char)*src;

		if ( ( c >= 'A' && c <= 'Z' ) || ( c >= 'a' && c <= 'z' ) || ( c >= '0' && c <= '9' ) ||
		     c == '-' || c == '_' || c == '.' || c == '~' )
		{
			dst[o++] = (char)c;
		}
		else
		{
			dst[o++] = '%';
			dst[o++] = hex[c >> 4];
			dst[o++] = hex[c & 15];
		}
	}
	dst[o] = '\000';
}

// Builds the complete "x-ladio-info: ...\n" header line (empty string when nothing is
// configured). Returns the code page the other YP headers have to be converted to
// (0 = send them unchanged).
static int ladioMakeHeader ( const char *charset, const char *dj, char *header, int headerSize )
{
	char	djConv[1024] = "";
	char	djEnc[3200] = "";
	char	info[3400] = "";
	int		codePage = ladioCodePage( charset );

	header[0] = '\000';

	if ( codePage != 0 ) sprintf( info, "charset=%s", charset );

	if ( dj[0] != '\000' )
	{
		const char	*sep = info[0] ? ";" : "";

		ladioConvert( dj, codePage, djConv, sizeof( djConv ) );
		ladioUrlEncode( djConv, djEnc, sizeof( djEnc ) );
		sprintf( info + strlen( info ), "%sdj=%s", sep, djEnc );
	}

	if ( info[0] != '\000' && (int)strlen( info ) + 20 < headerSize )
	{
		sprintf( header, "x-ladio-info: %s\n", info );
	}

	return codePage;
}

'''

ICE2_BLOCK = '''char	ladioInfo[4096] = "";
			char	ladioName[1024] = "";
			char	ladioURL[1024] = "";
			char	ladioGenre[1024] = "";
			char	ladioDesc[1024] = "";
			int		ladioCP = ladioMakeHeader( m_LadioCharset, m_LadioDJ, ladioInfo, sizeof( ladioInfo ) );

			ladioConvert( m_ServName, ladioCP, ladioName, sizeof( ladioName ) );
			ladioConvert( m_ServURL, ladioCP, ladioURL, sizeof( ladioURL ) );
			ladioConvert( m_ServGenre, ladioCP, ladioGenre, sizeof( ladioGenre ) );
			ladioConvert( m_ServDesc, ladioCP, ladioDesc, sizeof( ladioDesc ) );

			sprintf(contentString,
					"SOURCE %s ICE/1.0\\ncontent-type: %s\\nAuthorization: Basic %s\\nice-name: %s\\nice-url: %s\\nice-genre: %s\\nice-bitrate: %s\\nice-private: %d\\nice-public: %d\\nice-description: %s\\nice-audio-info: %s\\n%s\\n",
                    m_Mountpoint, contentType, puserAuthbase64, ladioName, ladioURL, ladioGenre, ypbrate, !m_PubServ, m_PubServ, ladioDesc, audioInfo, ladioInfo );'''


# Recording file name: "[YYYYMMDD] <stream name>_hhmmss"
FILENAME_BLOCK = r'''// Recording file name: "[YYYYMMDD] <Stream Name>_hhmmss"
	char_t     rawName[1024] = "";
	_snprintf( rawName, sizeof( rawName ) - 1, "[%04d%02d%02d] %s_%02d%02d%02d",
	           newtime->tm_year + 1900, newtime->tm_mon + 1, newtime->tm_mday, m_ServName,
	           newtime->tm_hour, newtime->tm_min, newtime->tm_sec );

	// Remove characters that are not allowed in file names. This looks at whole
	// multibyte characters only, so a Shift_JIS trail byte of 0x5C is kept intact.
	memset( outputFile, '\000', sizeof( outputFile ) );
	{
		const unsigned char	*p = (const unsigned char *)rawName;
		int					o = 0;

		while ( *p && o < (int)sizeof( outputFile ) - 16 )	// leave room for the extension
		{
			if ( IsDBCSLeadByte( *p ) && p[1] )
			{
				outputFile[o++] = (char)*p++;
				outputFile[o++] = (char)*p++;
			}
			else
			{
				char	c = (char)*p++;

				if ( c == '"' ) c = '\'';
				else if ( strchr( "\\/:*?<>|\r\n", c ) ) continue;
				outputFile[o++] = c;
			}
		}
	}'''


# --------------------------------------------------------------- libshuicast.cpp
def p_lib_cpp(t):
    # --- recording file name (replaces the whole sanitizing chain) ---
    t = edit(
        t,
        r'wsprintf\(\s*outFilename,\s*"%s_%s",\s*m_ServDesc,\s*asctime\(\s*newtime\s*\)\s*\);'
        r".*?strcpy\(outputFile, outFilename\);",
        FILENAME_BLOCK,
        "replace",
        "libshuicast.cpp OpenArchiveFile",
        re.S,
    )
    # --- Netladio ---
    t = edit(
        t,
        r"int CEncoder::ConnectToServer \(\)\s*\{\n",
        LADIO_HELPERS,
        "before",
        "libshuicast.cpp ConnectToServer",
    )
    t = edit(
        t,
        r"contentString\[1024\]",
        "contentString[8192]",
        "replace",
        "libshuicast.cpp contentString size",
    )
    t = edit(
        t,
        r'sprintf\(contentString,\s*"SOURCE %s ICE/1\.0.*?audioInfo \);',
        ICE2_BLOCK,
        "replace",
        "libshuicast.cpp ICE/1.0 request",
        re.S,
    )
    t = edit(
        t,
        r'[ \t]*GetConfigVariable\( gAppName, "ServerGenre", "Rock",[^\n]*\n',
        '    GetConfigVariable( gAppName, "ServerLadioDJ", "", m_LadioDJ, sizeof( m_LadioDJ ), NULL );\n'
        '    GetConfigVariable( gAppName, "ServerLadioCharset", "", m_LadioCharset, sizeof( m_LadioCharset ), NULL );\n',
        "after",
        "libshuicast.cpp GetConfigVariable",
    )
    t = edit(
        t,
        r'[ \t]*PutConfigVariable\( gAppName, "ServerGenre", m_ServGenre \);\n',
        '    PutConfigVariable( gAppName, "ServerLadioDJ", m_LadioDJ );\n'
        '    PutConfigVariable( gAppName, "ServerLadioCharset", m_LadioCharset );\n',
        "after",
        "libshuicast.cpp PutConfigVariable",
    )
    t = edit(
        t,
        r'[ \t]*AddConfigVariable\( "ServerGenre" \);\n',
        '    AddConfigVariable( "ServerLadioDJ" );\n'
        '    AddConfigVariable( "ServerLadioCharset" );\n',
        "after",
        "libshuicast.cpp AddConfigVariable",
    )
    return t


patch_file(APP / "resource.h", p_resource_h)
patch_file(APP / "shuicast.rc", p_rc)
patch_file(APP / "YPSettings.h", p_yp_h)
patch_file(APP / "YPSettings.cpp", p_yp_cpp)
patch_file(APP / "Config.cpp", p_config_cpp)
patch_file(LIB / "libshuicast.h", p_lib_h)
patch_file(LIB / "libshuicast.cpp", p_lib_cpp)
print("shuicast_patch: done")
