-- The standalone live board: the Kindle fetches each price itself and prints the board, with no server.
-- Run by paper.sh with the Kindle's own Lua 5.1:
--   lua live.lua <symbols.txt> <fbink> <xh> <cacert.pem> <work dir> [seconds between fetches]
-- symbols.txt comes with the hourly bundle: one "symbol|symbol for a URL|name|kind" per line.
--
-- It is a test bed for how much a 2012 Kindle can do alone, so it measures itself: every fetch and its time go
-- to the log, and the foot of the board shows the running averages. One price is fetched at a time (each is its
-- own HTTPS request to Yahoo Finance), in the background, so the clock keeps ticking while it is in flight.

local SYMS, FBINK, XH, CA, WORK = arg[1], arg[2], arg[3], arg[4], arg[5] or "/tmp/salive"
local STEP = tonumber(arg[6] or os.getenv("LIVE_STEP") or "") or 2 -- seconds between one fetch ending and the next
local FIFO = os.getenv("PAPER_FIFO") or "/tmp/paper.fifo"

-- (globals, not locals: a test replaces them)
function sh(cmd) return os.execute(cmd) end
function capture(cmd)
  local p = io.popen(cmd)
  if not p then return "" end
  local out = p:read("*a") or ""
  p:close()
  return out
end
function uptime() -- seconds, to a hundredth
  local f = io.open("/proc/uptime")
  if not f then return os.time() end
  local v = tonumber((f:read("*l") or ""):match("^([%d%.]+)"))
  f:close()
  return v or os.time()
end

local function quote(s) return "'" .. tostring(s):gsub("'", "'\\''") .. "'" end
local function ascii(s) return (s:gsub("[^\32-\126]", "")) end
local function log(s) io.stdout:write(os.date("%Y-%m-%d %H:%M:%S"), " live: ", s, "\n") io.stdout:flush() end

-- The text grid: the largest of FBInk's sizes that leaves 30 columns.
local size, cols, rows = 2, 47, 64
for _, s in ipairs({ 4, 3, 2 }) do
  local e = capture(quote(FBINK) .. " -e -S " .. s .. " 2>/dev/null")
  local c, r = tonumber(e:match("MAXCOLS=(%d+)")), tonumber(e:match("MAXROWS=(%d+)"))
  if c and r and c >= 30 then size, cols, rows = s, c, r break end
end

local draws, draw_time = 0, 0
local function put(row, text, extra)
  local t = uptime()
  sh(quote(FBINK) .. " -q -S " .. size .. " -W DU " .. (extra or "") .. " -y " .. row .. " -p " ..
    quote(ascii(text):sub(1, cols)) .. " 2>/dev/null")
  draws, draw_time = draws + 1, draw_time + (uptime() - t)
end

local function pad(s, n) s = s:sub(1, n) return s .. string.rep(" ", n - #s) end
local function rpad(s, n) s = s:sub(-n) return string.rep(" ", n - #s) .. s end

local function commas(s) -- 12345.67 -> 12,345.67
  local int, frac = s:match("^(%d+)(%.?%d*)$")
  if not int then return s end
  return int:reverse():gsub("(%d%d%d)", "%1,"):reverse():gsub("^,", "") .. frac
end
local function money(v)
  if v >= 1000 then return commas(string.format("%.2f", v)) end
  if v < 10 then return string.format("%.4f", v) end
  return string.format("%.2f", v)
end

-- The watchlist, and as many of them as the board has rows for.
local list = {}
for line in io.lines(SYMS) do
  local sym, enc, name = line:match("^([^|]+)|([^|]+)|([^|]*)|")
  if sym then list[#list + 1] = { sym = sym, enc = enc, name = ascii(name) } end
end
local TOP = 4
local room = rows - TOP - 4
local more = math.max(0, #list - room)
while #list > room do table.remove(list) end

local NAME = cols - 19 -- name, then 10 for the price and 8 for the move
local function row_text(it)
  if not it.price then return pad(it.name, NAME) .. rpad(it.err and "no quote" or "...", 10) end
  local move = it.pct and string.format("%+.2f%%", it.pct) or ""
  return pad(it.name, NAME) .. rpad(money(it.price), 10) .. rpad(move, 9)
end

sh("mkdir -p " .. quote(WORK))
sh(quote(FBINK) .. " -q -c -f 2>/dev/null")
put(0, pad("STOP PRESS LIVE", cols - 8) .. os.date("%H:%M:%S"))
put(1, "prices fetched by this Kindle itself")
put(3, pad("", NAME) .. rpad("PRICE", 10) .. rpad("DAY", 9), "-h")
for i, it in ipairs(list) do put(TOP + i - 1, row_text(it)) end
put(rows - 1, "TAP ANYWHERE: BACK TO THE PAPER", "-h")
log(string.format("board %dx%d at size %d, %d instruments%s", cols, rows, size, #list,
  more > 0 and (" (" .. more .. " more don't fit)") or ""))

local OUT, DONE = WORK .. "/q.json", WORK .. "/done"
local function start_fetch(it)
  os.remove(OUT) os.remove(DONE)
  local url = "https://query1.finance.yahoo.com/v8/finance/chart/" .. it.enc .. "?range=1d&interval=1d"
  sh("(" .. quote(XH) .. " -d -q --timeout 15 --verify " .. quote(CA) .. " -o " .. quote(OUT) .. " get " .. quote(url) ..
    " 'User-Agent:Mozilla/5.0' 'Accept-Encoding:identity' >/dev/null 2>&1; echo $? >" .. quote(DONE) .. ") &")
end
local function exists(path)
  local f = io.open(path)
  if f then f:close() return true end
  return false
end
local function read_quote(it)
  local f = io.open(OUT)
  local body = f and f:read("*a") or ""
  if f then f:close() end
  local price = tonumber(body:match('"regularMarketPrice":([%d%.eE+-]+)'))
  local prev = tonumber(body:match('"previousClose":([%d%.eE+-]+)')) or tonumber(body:match('"chartPreviousClose":([%d%.eE+-]+)'))
  if not price then it.err = true return false end
  it.err, it.price = nil, price
  it.pct = prev and prev ~= 0 and (price - prev) / prev * 100 or nil
  return true
end

local at, flying, started, wait_until = 0, nil, 0, 0
local run_fails = 0
local fetches, fails, fetch_time, round_start, round_time = 0, 0, 0, uptime(), nil
local last_clock, ticks, last_clean = "", 0, uptime()
local batt = ""

while true do
  local now = uptime()
  local clock = os.date("%H:%M:%S")
  if clock ~= last_clock then
    sh(quote(FBINK) .. " -q -S " .. size .. " -W DU -y 0 -x " .. (cols - 8) .. " " .. quote(clock) .. " 2>/dev/null")
    last_clock = clock
  end

  if flying and (exists(DONE) or now - started > 20) then
    local it, took = flying, now - started
    flying = nil
    local before = row_text(it)
    local ok = exists(DONE) and read_quote(it)
    if not ok then it.err = true end
    fetches, fetch_time = fetches + 1, fetch_time + took
    if not ok then
      fails, run_fails = fails + 1, run_fails + 1
      -- cacert-fast.pem holds only a few roots: if Yahoo stops verifying with it, use the full list
      local full = CA:gsub("[^/]*$", "cacert.pem")
      if run_fails >= 3 and CA ~= full and exists(full) then
        log("3 failures in a row: trying the full cacert.pem")
        CA = full
      end
    else
      run_fails = 0
    end
    log(string.format("%s %.2fs %s", it.sym, took, ok and money(it.price) or "FAILED"))
    if row_text(it) ~= before then put(TOP + it.row - 1, row_text(it)) end
    wait_until = now + STEP
    if it.row == #list then round_time, round_start = now - round_start, now end
  elseif not flying and now >= wait_until and #list > 0 then
    at = at % #list + 1
    flying, started = list[at], now
    flying.row = at
    start_fetch(flying)
  end

  ticks = ticks + 1
  if ticks % 15 == 1 then -- the test bed's own numbers
    local b = capture("lipc-get-prop com.lab126.powerd battLevel 2>/dev/null"):match("%d+")
    local charging = capture("lipc-get-prop com.lab126.powerd isCharging 2>/dev/null"):match("%d+")
    batt = b and (b .. "%" .. (charging == "1" and "+" or "")) or "?"
    put(rows - 3, string.format("fetch %.1fs avg  %d ok %d failed", fetches > 0 and fetch_time / fetches or 0,
      fetches - fails, fails))
    put(rows - 2, string.format("round %s  draw %.2fs  batt %s", round_time and string.format("%.0fs", round_time) or "-",
      draws > 0 and draw_time / draws or 0, batt))
    if b and tonumber(b) <= 20 and charging ~= "1" then
      log("battery low: live board off")
      sh("echo 'liveoff 0 " .. os.time() .. "' >" .. quote(FIFO))
      break
    end
  end
  if now - last_clean > 600 then -- the quick updates leave ghosts: one clean flash every ten minutes
    sh(quote(FBINK) .. " -q -f -s 2>/dev/null")
    last_clean = now
    log(string.format("stats: %d fetches, %d failed, %.2fs avg fetch, %.2fs avg draw, round %s, battery %s", fetches, fails,
      fetches > 0 and fetch_time / fetches or 0, draws > 0 and draw_time / draws or 0,
      round_time and string.format("%.0fs", round_time) or "-", batt))
  end
  sh("sleep 1")
end
