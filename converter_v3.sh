#!/bin/bash
# ================================================
# Jianying Draft -> FCP7 XML Converter TUI v3.0
# macOS / Linux version
# ================================================

set +e  # Don't exit on errors, handle them ourselves

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/jianying_to_xml_v3.py"
OUTPUT_DIR="$SCRIPT_DIR/output"
DRAFT_DIR=""
PYTHON_CMD=""

# Export settings
DO_XML="YES"
DO_SUBS=""
SUB_FMT="srt,ass,stl,txt"
DO_JSON=""

# -- Colors --
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
DIM='\033[0;90m'
BOLD='\033[1m'
NC='\033[0m'

# -- Helpers --
banner() {
    echo ""
    echo -e "  ${BLUE}============================================${NC}"
    echo -e "  ${BOLD}${BLUE}     Jianying Draft --> FCP7 XML${NC}"
    echo -e "  ${BLUE}     Jianying to XML Converter v3.0${NC}"
    echo -e "  ${BLUE}============================================${NC}"
    echo ""
}

status_line() {
    echo -e "  ${DIM}--------------------------------------------${NC}"
    if [ -n "$DRAFT_DIR" ]; then
        echo -e "  [DRAFT] ${GREEN}$DRAFT_DIR${NC}"
    else
        echo -e "  [DRAFT] ${YELLOW}NOT SET${NC} - Please select first"
    fi
    echo -e "  [OUTPUT] $OUTPUT_DIR"
    echo ""
    # Conditional color: variable stores logic value, color injected at display time
    xml_clr=$([ "$DO_XML" = "YES" ] && echo "$GREEN" || echo "")
    sub_clr=$([ -n "$DO_SUBS" ] && echo "$GREEN" || echo "")
    json_clr=$([ -n "$DO_JSON" ] && echo "$GREEN" || echo "")
    xml_txt=$([ "$DO_XML" = "YES" ] && echo "ON" || echo "OFF")
    sub_txt=$([ -n "$DO_SUBS" ] && echo "ON" || echo "OFF")
    json_txt=$([ -n "$DO_JSON" ] && echo "ON" || echo "OFF")
    echo -e "    XML:       ${xml_clr}${xml_txt}${NC}"
    echo -e "    Subtitles: ${sub_clr}${sub_txt}${NC}  [$SUB_FMT]"
    echo -e "    JSON:      ${json_clr}${json_txt}${NC}"
}

check_python() {
    if [ -n "$PYTHON_CMD" ]; then
        return 0
    fi
    for cmd in python3 python python3.11 python3.10 python3.9; do
        if command -v "$cmd" &>/dev/null; then
            # Verify it's actually Python (not Python2 stub)
            if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" 2>/dev/null; then
                PYTHON_CMD="$cmd"
                return 0
            fi
        fi
    done
    return 1
}

show_python_guide() {
    echo ""
    echo -e "  ${RED}[ERROR] Python 3.8+ not found!${NC}"
    echo ""
    echo -e "  ${DIM}Please install Python 3.8 or newer:${NC}"
    echo ""
    local OS
    OS="$(uname -s)"
    if [ "$OS" = "Darwin" ]; then
        echo -e "  ${CYAN}Option A (Homebrew - recommended):${NC}"
        echo "    brew install python3"
        echo ""
        echo -e "  ${CYAN}Option B (Official installer):${NC}"
        echo "    https://www.python.org/downloads/mac-osx/"
    else
        echo -e "  ${CYAN}Ubuntu/Debian:${NC}"
        echo "    sudo apt update && sudo apt install python3 python3-pip"
        echo ""
        echo -e "  ${CYAN}Fedora/RHEL:${NC}"
        echo "    sudo dnf install python3"
        echo ""
        echo -e "  ${CYAN}Arch Linux:${NC}"
        echo "    sudo pacman -S python"
    fi
    echo ""
}

# -- Scan for drafts --
scan_roots() {
    local -a roots=()
    local home="$HOME"

    if [ "$(uname -s)" = "Darwin" ]; then
        for sub in "Movies" "Documents"; do
            for app in "JianyingPro" "CapCut"; do
                roots+=("$home/$sub/$app/User Data/Projects/com.lveditor.draft")
                roots+=("$home/$sub/$app/User Data/Projects/compositon")
            done
        done
    else
        for base in "$home/.local/share" "$home/.config"; do
            for app in "JianyingPro" "CapCut"; do
                roots+=("$base/$app/User Data/Projects/com.lveditor.draft")
                roots+=("$base/$app/User Data/Projects/compositon")
            done
        done
    fi

    # WSL support
    if [ -d "/mnt/c" ]; then
        for drv in /mnt/c /mnt/d /mnt/e /mnt/f; do
            for uname_dir in "$drv"/Users/*/; do
                local u
                u=$(basename "$uname_dir")
                local appdata="$drv/Users/$u/AppData/Local/JianyingPro/User Data/Projects"
                for sub in "com.lveditor.draft" "compositon"; do
                    roots+=("$appdata/$sub")
                done
            done
        done
    fi

    printf '%s\n' "${roots[@]}"
}

# ==========================================
# Main menu
# ==========================================
main() {
    while true; do
        clear
        banner
        status_line
        echo ""
        echo -e "  ${DIM}--------------------------------------------${NC}"
        echo ""
        echo -e "  ${CYAN}[1]${NC} Select draft folder (paste path)"
        echo -e "  ${CYAN}[2]${NC} Auto scan drafts"
        echo -e "  ${CYAN}[3]${NC} Set output directory"
        echo -e "  ${CYAN}[4]${NC} Export settings"
        echo -e "  ${GREEN}[5]${NC} ${BOLD}START CONVERT${NC}"
        echo -e "  [0] Quit"
        echo ""
        echo -e "  ${DIM}--------------------------------------------${NC}"
        echo ""
        read -rp "  > " choice

        case "$choice" in
            1) select_path ;;
            2) auto_detect ;;
            3) set_output ;;
            4) settings ;;
            5) convert ;;
            0) clear; exit 0 ;;
            *) echo -e "  ${RED}Invalid option${NC}"; sleep 1 ;;
        esac
    done
}

# -- Option 1: Select path --
select_path() {
    clear
    echo ""
    echo -e "  ${BLUE}============================================${NC}"
    echo -e "  ${BLUE}  Select Jianying Draft Path${NC}"
    echo -e "  ${BLUE}============================================${NC}"
    echo ""
    echo -e "  ${DIM}You can:${NC}"
    echo -e "  ${DIM}  - Paste the full path below${NC}"
    echo -e "  ${DIM}  - Type a keyword to search${NC}"
    echo ""
    read -rp "  > " input_path

    input_path="${input_path//\"/}"
    input_path="${input_path//\'/}"

    if [ -z "$input_path" ]; then
        return
    fi

    if [ -d "$input_path" ]; then
        DRAFT_DIR="$input_path"
        echo -e "\n  ${GREEN}Set to: $DRAFT_DIR${NC}"
        sleep 2
    elif [ -f "$input_path" ]; then
        DRAFT_DIR="$(dirname "$input_path")"
        echo -e "\n  ${GREEN}Set to: $DRAFT_DIR${NC}"
        sleep 2
    else
        echo -e "\n  ${YELLOW}Path not found. Searching...${NC}\n"
        local found=0
        for search_dir in \
            "$HOME/Movies/JianyingPro/User Data/Projects/com.lveditor.draft" \
            "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft" \
            "$HOME/.local/share/JianyingPro/User Data/Projects/com.lveditor.draft"; do
            [ -d "$search_dir" ] || continue
            for d in "$search_dir"/*/; do
                [ -d "$d" ] || continue
                local bname
                bname=$(basename "$d")
                if echo "$bname" | grep -qi "$input_path"; then
                    echo -e "  ${GREEN}Match: $bname${NC}"
                    echo "         $d"
                    DRAFT_DIR="$d"
                    found=1
                    break 2
                fi
            done
        done
        if [ "$found" -eq 0 ]; then
            echo -e "  ${RED}No match found.${NC}"
        fi
        sleep 2
    fi
}

# -- Option 2: Auto detect --
auto_detect() {
    clear
    echo ""
    echo -e "  ${BLUE}============================================${NC}"
    echo -e "  ${BLUE}  Scanning for Jianying drafts...${NC}"
    echo -e "  ${BLUE}============================================${NC}"
    echo ""

    local -a paths=()
    local n=0

    while IFS= read -r root; do
        [ -d "$root" ] || continue
        for dir in "$root"/*/; do
            [ -d "$dir" ] || continue
            [ -f "${dir}draft_content.json" ] || [ -f "${dir}template.json.bak" ] || continue
            # Dedup by realpath
            local dup=0
            local rp
            rp=$(realpath "$dir" 2>/dev/null) || rp="$dir"
            for existing in "${paths[@]+"${paths[@]}"}"; do
                local erp
                erp=$(realpath "$existing" 2>/dev/null) || erp="$existing"
                if [ "$rp" = "$erp" ]; then
                    dup=1
                    break
                fi
            done
            [ "$dup" -eq 1 ] && continue
            n=$((n + 1))
            paths+=("$dir")
            echo -e "  ${CYAN}[$n]${NC} $(basename "$dir")"
            echo -e "       ${DIM}$dir${NC}"
        done
    done < <(scan_roots)

    if [ "$n" -eq 0 ]; then
        echo -e "  ${RED}No drafts found.${NC}"
        echo ""
        echo -e "  ${DIM}Tip: Add your Jianying projects path to config.json${NC}"
        sleep 2
        return
    fi

    echo ""
    echo -e "  ${DIM}Found $n draft(s)${NC}"
    echo ""
    read -rp "  Select [1-$n]: " sel

    if [ -z "$sel" ] || ! [[ "$sel" =~ ^[0-9]+$ ]] || [ "$sel" -lt 1 ] || [ "$sel" -gt "$n" ]; then
        return
    fi

    DRAFT_DIR="${paths[$((sel - 1))]}"
    echo -e "\n  ${GREEN}Selected: $DRAFT_DIR${NC}"
    sleep 2
}

# -- Option 3: Set output --
set_output() {
    clear
    echo ""
    echo -e "  ${BLUE}============================================${NC}"
    echo -e "  ${BLUE}  Set Output Directory${NC}"
    echo -e "  ${BLUE}============================================${NC}"
    echo ""
    echo -e "  Current: $OUTPUT_DIR"
    echo ""
    echo "  [1] Keep current"
    echo "  [2] Script folder /output"
    echo "  [3] Same as draft folder"
    echo "  [4] Custom path"
    echo ""
    read -rp "  > " opt

    case "$opt" in
        1) return ;;
        2)
            OUTPUT_DIR="$SCRIPT_DIR/output"
            mkdir -p "$OUTPUT_DIR"
            echo -e "  ${GREEN}Set to: $OUTPUT_DIR${NC}"
            sleep 1 ;;
        3)
            if [ -n "$DRAFT_DIR" ]; then
                OUTPUT_DIR="$DRAFT_DIR"
                echo -e "  ${GREEN}Set to: $OUTPUT_DIR${NC}"
            else
                echo -e "  ${YELLOW}Please select draft first.${NC}"
            fi
            sleep 1 ;;
        4)
            read -rp "  Path: " custom
            custom="${custom//\"/}"
            if [ -n "$custom" ]; then
                mkdir -p "$custom" 2>/dev/null
                OUTPUT_DIR="$custom"
                echo -e "  ${GREEN}Set to: $OUTPUT_DIR${NC}"
            fi
            sleep 1 ;;
    esac
}

# -- Option 4: Export settings --
settings() {
    while true; do
        clear
        echo ""
        echo -e "  ${BLUE}============================================${NC}"
        echo -e "  ${BLUE}  Export Settings${NC}"
        echo -e "  ${BLUE}============================================${NC}"
        echo ""
        xml_clr=$([ "$DO_XML" = "YES" ] && echo "$GREEN" || echo "")
        sub_clr=$([ -n "$DO_SUBS" ] && echo "$GREEN" || echo "")
        json_clr=$([ -n "$DO_JSON" ] && echo "$GREEN" || echo "")
        xml_txt=$([ "$DO_XML" = "YES" ] && echo "[ON]" || echo "[OFF]")
        sub_txt=$([ -n "$DO_SUBS" ] && echo "[ON]" || echo "[OFF]")
        json_txt=$([ -n "$DO_JSON" ] && echo "[ON]" || echo "[OFF]")
        echo -e "  [1] FCP7 XML:      ${xml_clr}${xml_txt}${NC}"
        echo -e "  [2] Subtitles:     ${sub_clr}${sub_txt}${NC}  formats: $SUB_FMT"
        echo -e "  [3] Timeline JSON: ${json_clr}${json_txt}${NC}"
        echo "  [0] Back"
        echo ""
        read -rp "  > " set_opt

        case "$set_opt" in
            0) return ;;
            1)
                if [ "$DO_XML" = "YES" ]; then
                    DO_XML=""
                    echo -e "  XML: OFF"
                else
                    DO_XML="YES"
                    echo -e "  ${GREEN}XML: ON${NC}"
                fi
                sleep 1 ;;
            2)
                if [ -n "$DO_SUBS" ]; then
                    DO_SUBS=""
                    echo -e "  Subtitles: OFF"
                else
                    DO_SUBS="YES"
                    echo ""
                    echo "  Available formats:"
                    echo "    [1] SRT"
                    echo "    [2] ASS"
                    echo "    [3] STL"
                    echo "    [4] TXT"
                    echo ""
                    echo "  Select (e.g. 12 = SRT+ASS, 134 = SRT+STL+TXT, Enter = all):"
                    read -rp "  > " fmt_sel
                    SUB_FMT="srt,ass,stl,txt"
                    if [ -n "$fmt_sel" ]; then
                        SUB_FMT=""
                        echo "$fmt_sel" | grep -q "1" && SUB_FMT="${SUB_FMT}srt,"
                        echo "$fmt_sel" | grep -q "2" && SUB_FMT="${SUB_FMT}ass,"
                        echo "$fmt_sel" | grep -q "3" && SUB_FMT="${SUB_FMT}stl,"
                        echo "$fmt_sel" | grep -q "4" && SUB_FMT="${SUB_FMT}txt,"
                        [ -z "$SUB_FMT" ] && SUB_FMT="srt,ass,stl,txt"
                        SUB_FMT="${SUB_FMT%,}"  # Remove trailing comma
                    fi
                    echo -e "  ${GREEN}Subtitles: ON  formats: $SUB_FMT${NC}"
                fi
                sleep 1 ;;
            3)
                if [ -n "$DO_JSON" ]; then
                    DO_JSON=""
                    echo -e "  JSON: OFF"
                else
                    DO_JSON="YES"
                    echo -e "  ${GREEN}JSON: ON${NC}"
                fi
                sleep 1 ;;
        esac
    done
}

# -- Option 5: Convert --
convert() {
    if [ -z "$DRAFT_DIR" ]; then
        echo -e "\n  ${RED}[ERROR] No draft selected. Use option 1 or 2 first.${NC}"
        sleep 2
        return
    fi

    if [ ! -d "$DRAFT_DIR" ]; then
        echo -e "\n  ${RED}[ERROR] Draft directory not found: $DRAFT_DIR${NC}"
        sleep 2
        return
    fi

    # Check at least one export mode enabled
    if [ "$DO_XML" != "YES" ] && [ -z "$DO_SUBS" ] && [ -z "$DO_JSON" ]; then
        echo -e "\n  ${RED}[ERROR] No export mode enabled. Use option 4 to configure.${NC}"
        sleep 2
        return
    fi

    if ! check_python; then
        show_python_guide
        read -rp "  Press Enter to continue..."
        return
    fi

    mkdir -p "$OUTPUT_DIR"

    # Build arguments
    local py_args=""
    if [ -n "$DO_SUBS" ]; then
        py_args="$py_args -f $SUB_FMT"
    fi
    if [ "$DO_XML" = "YES" ]; then
        py_args="$py_args --xml"
    fi
    if [ -n "$DO_JSON" ]; then
        py_args="$py_args --json"
    fi

    local cmd="$PYTHON_CMD \"$SCRIPT\" \"$DRAFT_DIR\" -o \"$OUTPUT_DIR\" $py_args"

    clear
    echo ""
    echo -e "  ${BLUE}============================================${NC}"
    echo -e "  ${BLUE}  Converting...${NC}"
    echo -e "  ${BLUE}============================================${NC}"
    echo ""
    echo -e "  Draft:  $DRAFT_DIR"
    echo -e "  Output: $OUTPUT_DIR"
    echo ""
    echo -e "  ${DIM}--------------------------------------------${NC}"
    echo ""

    eval "$cmd"

    echo ""
    echo -e "  ${DIM}--------------------------------------------${NC}"

    if [ $? -eq 0 ]; then
        echo ""
        echo -e "  ${GREEN}${BOLD}============================================${NC}"
        echo -e "  ${GREEN}${BOLD}  DONE!${NC}"
        echo -e "  ${GREEN}${BOLD}============================================${NC}"
        echo ""
        echo -e "  Output: $OUTPUT_DIR"
        echo ""

        # List generated files
        for f in "$OUTPUT_DIR"/*.xml; do
            [ -f "$f" ] && echo -e "  ${GREEN}[XML]${NC}  $(basename "$f")"
        done
        for f in "$OUTPUT_DIR"/*_timeline.json; do
            [ -f "$f" ] && echo -e "  ${GREEN}[JSON]${NC} $(basename "$f")"
        done
        for ext in srt ass stl txt; do
            for f in "$OUTPUT_DIR"/*."$ext"; do
                [ -f "$f" ] && echo -e "  ${GREEN}[$(echo "$ext" | tr 'a-z' 'A-Z')]${NC}  $(basename "$f")"
            done
        done

        echo ""
        echo -e "  ${DIM}DaVinci Resolve: File > Import Timeline > Import AAF, EDL, XML...${NC}"
        echo ""

        local open_ans
        read -rp "  Open output folder? (Y/n): " open_ans
        if [[ ! "$open_ans" =~ ^[nN] ]]; then
            if [ "$(uname -s)" = "Darwin" ]; then
                open "$OUTPUT_DIR"
            elif command -v xdg-open &>/dev/null; then
                xdg-open "$OUTPUT_DIR"
            fi
        fi
    else
        echo -e "\n  ${RED}[ERROR] Conversion failed.${NC}"
    fi

    echo ""
    read -rp "  Press Enter to continue..."
}

# -- Entry point --
main
