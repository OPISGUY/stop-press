-- Touch reader for Kindles with a touch screen (Kindle Touch, Paperwhite and later), run with the Kindle's own
-- Lua 5.1:  lua touch.lua /dev/input/event0 [raw]
-- Reads the screen's input events and prints one line per gesture, flushed straight away:
--   tap <x> <y> <unix time>          a short touch that didn't move far
--   swipe <left|right|up|down> <x> <y> <unix time>
--   hold <x> <y> <tenths of a second held>   a touch held still for over 1.5 s
-- with x, y in screen pixels where the finger went down. "raw" prints every event instead (type code value).

local dev, mode = arg[1] or "/dev/input/event0", arg[2]
local f = assert(io.open(dev, "rb"))
f:setvbuf("no")

local function u16(s, i)
  local a, b = s:byte(i, i + 1)
  return a + b * 256
end

local function s32(s, i)
  local a, b, c, d = s:byte(i, i + 3)
  local v = a + b * 256 + c * 65536 + d * 16777216
  if v >= 2147483648 then v = v - 4294967296 end
  return v
end

local EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
local ABS_X, ABS_Y, MT_X, MT_Y, MT_TRACKING = 0, 1, 53, 54, 57
local BTN_TOUCH = 330
local SWIPE = tonumber(os.getenv("SWIPE_PX") or "") or 120 -- how far a finger must travel to be a swipe
local SX = tonumber(os.getenv("TOUCH_SCALE_X") or "") or 1 -- raw touch units to screen pixels, if they differ
local SY = tonumber(os.getenv("TOUCH_SCALE_Y") or "") or 1

local x, y = 0, 0
local x0, y0 = nil, nil
local touching, starting = false, false
local t, t0 = 0, 0 -- the current event's time and the touch's start, in tenths of a second

local function emit(s)
  io.stdout:write(s, "\n")
  io.stdout:flush()
end

local function finish()
  if x0 then
    local dx, dy = (x - x0) * SX, (y - y0) * SY
    local px, py = math.floor(x0 * SX + 0.5), math.floor(y0 * SY + 0.5)
    local held = t - t0
    if held >= 15 and math.abs(dx) < SWIPE / 2 and math.abs(dy) < SWIPE / 2 then
      emit(string.format("hold %d %d %d", px, py, held))
    elseif math.abs(dx) >= SWIPE and math.abs(dx) >= math.abs(dy) then
      emit(string.format("swipe %s %d %d %d", dx < 0 and "left" or "right", px, py, os.time()))
    elseif math.abs(dy) >= SWIPE then
      emit(string.format("swipe %s %d %d %d", dy < 0 and "up" or "down", px, py, os.time()))
    else
      emit(string.format("tap %d %d %d", px, py, os.time()))
    end
  end
  x0, y0, touching, starting = nil, nil, false, false
end

while true do
  local e = f:read(16)
  if not e or #e < 16 then break end
  local typ, code, val = u16(e, 9), u16(e, 11), s32(e, 13)
  t = s32(e, 1) * 10 + math.floor(s32(e, 5) / 100000)
  if mode == "raw" then
    emit(string.format("%d %d %d", typ, code, val))
  elseif typ == EV_ABS then
    if code == ABS_X or code == MT_X then x = val
    elseif code == ABS_Y or code == MT_Y then y = val
    elseif code == MT_TRACKING then
      if val >= 0 and not touching then touching, starting = true, true
      elseif val < 0 and touching then finish() end
    end
  elseif typ == EV_KEY and code == BTN_TOUCH then
    if val == 1 and not touching then touching, starting = true, true
    elseif val == 0 and touching then finish() end
  elseif typ == EV_SYN and starting then
    x0, y0, t0, starting = x, y, t, false -- the first full position report of this touch
  end
end
