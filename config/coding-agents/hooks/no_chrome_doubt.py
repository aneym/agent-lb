import re

NAMES = r"google chrome|chromium|chrome|safari|aside|firefox|brave browser|brave|microsoft edge|vivaldi|arc|opera|orion"
BROWSER_ACTION = re.compile(
    r"(?i)(google chrome|chromium|safari|firefox|brave browser|microsoft edge|vivaldi|arc|opera|orion|aside)\.app\b"
    r"|com\.google\.chrome|com\.apple\.safari|org\.chromium|org\.mozilla\.firefox|com\.brave\.browser"
    r"|com\.microsoft\.edgemac|com\.vivaldi\.vivaldi|company\.thebrowser\.browser|com\.operasoftware|com\.kagi\.kagimacos"
    r"|(?<![\w.-])(google-chrome(-stable|-beta|-unstable)?|chromium(-browser)?|chrome|safari|firefox(-esr)?"
    r"|brave(-browser)?|microsoft-edge(-stable|-beta|-dev)?|msedge|vivaldi(-stable)?|sensible-browser|x-www-browser"
    r"|www-browser)(\.exe)?(?![\w-])"
    r"|\bopen\b[^;&|\n]*\s-[a-z]*[ab][a-z]*\s+[\"']?([^\s\"']*/)?(" + NAMES + r"|com\.)"
    r"|\bapp(lication)?\s*(\(\s*|\s+(id\s+)?)[\"']?(" + NAMES + r")\b|\bopen\s+location\b|openLocation"
    r"|((?<![\w.-])(xdg-|gnome-|kde-)?open5?|\bgio\s+open|\bwslview)\s+(-\S+\s+)*[\"']?(https?|file|ftp|about|data|javascript|view-source):"
    r"|remote-debugging|connect_?over_?cdp|chrome-remote-interface|puppeteer|playwright|selenium|webdriver"
    r"|--user-data-dir|--profile-directory|--storage-state|--cdp\b"
    r"|Library/Application Support/(Google|Chromium|Firefox|BraveSoftware|Microsoft Edge|Arc|Vivaldi|Aside|com\.operasoftware)"
    r"|Library/(Safari|Cookies|Containers/com\.apple\.Safari)"
)


def kind(command):
    return "browser" if BROWSER_ACTION.search(command) else "doubt"
