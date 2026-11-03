#!/usr/bin/env bash
# Reachability probe for every upstream source. Uses the poller's honest
# User-Agent (no browser spoofing). Prints one line per URL.
set -u
UA="boulder-election-dashboard/1.0 (personal, non-commercial results dashboard; +https://github.com/mgingrich69/python)"
EID="${EID:-122598}"
mkdir -p probe
probe() {  # name url
  local code
  code=$(curl -sS -A "$UA" --max-time 30 -o "probe/$1" -w "%{http_code} %{size_download}B %{content_type}" "$2" 2>&1)
  echo "$1 :: $code :: $2"
}
probe boulder_index.html      https://electionresults.bouldercounty.gov/index.html
probe boulder_turnout.html    https://electionresults.bouldercounty.gov/Home/PrecinctTurnout.html
probe nc_results_0.json       https://er.ncsbe.gov/enr/20261103/data/results_0.txt
probe co_current_ver.txt      "https://results.enr.clarityelections.com/CO/${EID}/current_ver.txt"
VER=$(tr -dc '0-9' < probe/co_current_ver.txt 2>/dev/null | head -c 12)
if [ -n "$VER" ]; then
  probe co_detailxml.zip      "https://results.enr.clarityelections.com/CO/${EID}/${VER}/reports/detailxml.zip"
  probe co_electionsettings.json "https://results.enr.clarityelections.com/CO/${EID}/${VER}/json/electionsettings.json"
else
  echo "co_current_ver.txt did not return a version number; Clarity likely blocked or ID wrong"
  head -c 300 probe/co_current_ver.txt 2>/dev/null; echo
fi
exit 0
