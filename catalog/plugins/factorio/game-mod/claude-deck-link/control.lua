-- Noryx Link — статистика Factorio для дека на телефоне.
-- Раз в 5 с (300 тиков) снимает показатели поверхности (планеты), где стоит игрок, копит историю
-- (1 мин, 10 мин, 1 ч, 10 ч — по 12…60 точек) и пишет script-output/claude-deck/factorio.json.
-- Плагин дека читает этот файл. Мод ничего в игре не меняет (только читает статистику).
-- Скорости считаем сами по накопленным итогам (input_counts / output_counts): разница за 5 с.

local EVERY = 300          -- тиков между снимками (5 с)
local KEEP5 = 120          -- снимков по 5 с (10 мин)
local KEEP = 60            -- точек в часовой и десятичасовой истории
local TOP = 8              -- сколько строк в «топах»
local FILE = "claude-deck/factorio.json"

-- показатели: ключ → как считать среднее при сжатии истории (все — среднее)
local METRICS = {"pp", "pc", "pe", "pa", "pt", "im", "iu", "sc", "ev", "kl"}

local function init()
  storage.cd = storage.cd or {}
  storage.cd.s = storage.cd.s or {}          -- по поверхностям: история и прошлые итоги
end

local function surf(index)
  local s = storage.cd.s[index]
  if not s then
    s = {last = {}, b5 = {}, b60 = {}, b600 = {}, acc1 = {}, acc10 = {}, n5 = 0, n60 = 0, poles = {}, scan = 0}
    for _, m in ipairs(METRICS) do
      s.b5[m], s.b60[m], s.b600[m] = {}, {}, {}
      s.acc1[m], s.acc10[m] = {0, 0}, {0, 0}
    end
    storage.cd.s[index] = s
  end
  return s
end

local function push(list, v, keep)
  list[#list + 1] = v
  while #list > keep do table.remove(list, 1) end
end

-- разница накопленных итогов по каждому имени: {имя = прирост}, сумма, и запомнить новые итоги
local function deltas(last, key, counts)
  local prev = last[key] or {}
  local d, sum, fresh = {}, 0, last[key] == nil
  for name, c in pairs(counts or {}) do
    local inc = c - (prev[name] or 0)
    if inc < 0 then inc = 0 end              -- сеть разделилась / итог сброшен — не уходим в минус
    if not fresh and inc > 0 then
      d[name] = inc
      sum = sum + inc
    end
  end
  local copy = {}
  for name, c in pairs(counts or {}) do copy[name] = c end
  last[key] = copy
  return d, sum, fresh
end

local function top(d, mult)
  local list = {}
  for name, v in pairs(d) do list[#list + 1] = {name, v * mult} end
  table.sort(list, function(a, b) return a[2] > b[2] end)
  local out = {}
  for i = 1, math.min(TOP, #list) do out[i] = {list[i][1], math.floor(list[i][2] * 100 + 0.5) / 100} end
  return out
end

-- по столбу на каждую электросеть поверхности (раз в минуту — заново: сети строятся и делятся)
local function networks(surface, s)
  if s.scan <= 0 or not next(s.poles) then
    s.poles, s.scan = {}, 12
    for _, p in pairs(surface.find_entities_filtered{type = "electric-pole"}) do
      local id = p.electric_network_id
      if id and not s.poles[id] then s.poles[id] = p end
    end
  end
  s.scan = s.scan - 1
  local ids = {}
  for id, p in pairs(s.poles) do
    if p.valid and p.electric_network_id == id then ids[#ids + 1] = id else s.poles[id] = nil end
  end
  table.sort(ids)                            -- одна и та же сеть — всегда под меньшим номером (итоги не скачут)
  -- разные номера сетей могут отдавать одну и ту же статистику (у Sterben — 15 номеров на одну сеть: в 15 раз
  -- больше ватт) — одинаковые накопленные итоги считаем одной сетью
  local out, seen = {}, {}
  for _, id in ipairs(ids) do
    local st = s.poles[id].electric_network_statistics
    local a, b = 0, 0
    for _, c in pairs(st.input_counts or {}) do a = a + c end
    for _, c in pairs(st.output_counts or {}) do b = b + c end
    local sig = string.format("%.17g:%.17g", a, b)
    if a + b == 0 or not seen[sig] then
      seen[sig] = true
      out[#out + 1] = {id, st}
    end
  end
  s.nets = {ids = #ids, real = #out}         -- для настроек плагина: сколько номеров сетей и сколько из них разных
  return out
end

-- каждый показатель — отдельно под pcall: поменяется что-то в API игры (2.0 → 2.1 …) — пропадёт он один,
-- а не весь мод; текст ошибки уходит в файл (err), дек покажет его в настройках плагина
local function try(errs, what, fn)
  local ok, e = pcall(fn)
  if not ok then errs[#errs + 1] = what .. ": " .. tostring(e) end
end

local function sample(surface, force)
  local s = surf(surface.index)
  local sec = EVERY / 60
  local v, tops, errs = {}, {}, {}

  -- электричество: у сети наоборот, чем у предметов — input — потребители (энергия уходит в них), output — генераторы
  -- (паровые турбины, солнце…); итоги в джоулях → ватты
  try(errs, "power", function()
    local prod, cons, pd, cd = 0, 0, {}, {}
    for _, n in ipairs(networks(surface, s)) do
      local id, st = n[1], n[2]
      local a, sa = deltas(s.last, "pgen" .. id, st.output_counts)
      local b, sb = deltas(s.last, "puse" .. id, st.input_counts)
      prod, cons = prod + sa, cons + sb
      for k, x in pairs(a) do pd[k] = (pd[k] or 0) + x end
      for k, x in pairs(b) do cd[k] = (cd[k] or 0) + x end
    end
    v.pp, v.pc = prod / sec, cons / sec
    tops.pp, tops.pc = top(pd, 1 / sec), top(cd, 1 / sec)
  end)

  -- загрязнение: input — выбросы (по видам построек), output — поглощение; в минуту
  local ok, pst = pcall(function() return game.get_pollution_statistics(surface) end)
  if ok and pst then
    local e, se = deltas(s.last, "polin", pst.input_counts)
    local _, sa = deltas(s.last, "polout", pst.output_counts)
    v.pe, v.pa = se * 60 / sec, sa * 60 / sec
    tops.pe = top(e, 60 / sec)
  end
  try(errs, "pollution", function() v.pt = surface.get_total_pollution() end)

  -- предметы: input — сделано, output — потрачено; наука — потраченные …science-pack (их съедают лаборатории)
  try(errs, "items", function()
    local ist = force.get_item_production_statistics(surface)
    local made, sm = deltas(s.last, "itin", ist.input_counts)
    local used, su = deltas(s.last, "itout", ist.output_counts)
    v.im, v.iu = sm * 60 / sec, su * 60 / sec
    local sci, ss = {}, 0
    for name, x in pairs(used) do
      if string.find(name, "science%-pack$") then sci[name] = x; ss = ss + x end
    end
    v.sc = ss * 60 / sec
    tops.im, tops.iu, tops.sc = top(made, 60 / sec), top(used, 60 / sec), top(sci, 60 / sec)
  end)

  -- враги: эволюция (у каждой планеты своя) и убийства нашей силой в минуту
  local okE, evo = pcall(function() return game.forces.enemy.get_evolution_factor(surface) end)
  if not okE then okE, evo = pcall(function() return game.forces.enemy.evolution_factor end) end
  v.ev = okE and evo and evo * 100 or 0
  local okK, kst = pcall(function() return force.get_kill_count_statistics(surface) end)
  if okK and kst then
    local _, sk = deltas(s.last, "kill", kst.input_counts)
    v.kl = sk * 60 / sec
  else
    v.kl = 0
  end

  -- история: снимки по 5 с; средние за минуту и за 10 минут
  s.n5 = s.n5 + 1
  for _, m in ipairs(METRICS) do
    local x = v[m] or 0
    push(s.b5[m], x, KEEP5)
    local a = s.acc1[m]; a[1], a[2] = a[1] + x, a[2] + 1
  end
  if s.n5 % 12 == 0 then
    s.n60 = s.n60 + 1
    for _, m in ipairs(METRICS) do
      local a = s.acc1[m]
      local mean = a[2] > 0 and a[1] / a[2] or 0
      push(s.b60[m], mean, KEEP)
      s.acc1[m] = {0, 0}
      local b = s.acc10[m]; b[1], b[2] = b[1] + mean, b[2] + 1
    end
    if s.n60 % 10 == 0 then
      for _, m in ipairs(METRICS) do
        local b = s.acc10[m]
        push(s.b600[m], b[2] > 0 and b[1] / b[2] or 0, KEEP)
        s.acc10[m] = {0, 0}
      end
    end
  end
  return v, tops, s, errs
end

-- 10 минут: 120 снимков по 5 с → 60 точек по 10 с
local function halve(list)
  local out = {}
  for i = 1, #list - 1, 2 do out[#out + 1] = (list[i] + list[i + 1]) / 2 end
  if #list % 2 == 1 then out[#out + 1] = list[#list] end
  return out
end

local function last(list, n)
  local out = {}
  for i = math.max(1, #list - n + 1), #list do out[#out + 1] = list[i] end
  return out
end

local function round(x)
  if x >= 100 or x <= -100 then return math.floor(x + 0.5) end
  return math.floor(x * 100 + 0.5) / 100
end

local function rounded(list)
  local out = {}
  for i, x in ipairs(list) do out[i] = round(x) end
  return out
end

local function report(player, surface, v, tops, s, force, errs)
  local series = {}
  for _, m in ipairs(METRICS) do
    series[m] = {m1 = rounded(last(s.b5[m], 12)), m10 = rounded(halve(s.b5[m])), h1 = rounded(s.b60[m]), h10 = rounded(s.b600[m])}
  end
  local now = {}
  for _, m in ipairs(METRICS) do now[m] = round(v[m] or 0) end
  local research
  try(errs, "research", function()
    if force.current_research then
      research = {name = force.current_research.name, progress = round((force.research_progress or 0) * 100)}
    end
  end)
  local planet
  try(errs, "planet", function() planet = surface.planet and surface.planet.name or nil end)
  local out = {
    v = 1, tick = game.tick, surface = surface.name, planet = planet, version = script.active_mods and script.active_mods.base,
    player = player.name, every = EVERY / 60, now = now, series = series, top = tops, research = research,
    err = errs[1], nets = s.nets,
  }
  local json = helpers.table_to_json(out)
  -- только на машине этого игрока; если игра не примет номер игрока — пишем без него (одиночная игра)
  if not pcall(helpers.write_file, FILE, json, false, player.index) then helpers.write_file(FILE, json, false) end
end

local function tick()
  init()
  local done = {}
  for _, player in pairs(game.connected_players) do
    local surface, force = player.surface, player.force
    local key = surface.index .. ":" .. force.index
    if not done[key] then
      done[key] = {sample(surface, force)}
    end
    local r = done[key]
    report(player, surface, r[1], r[2], r[3], force, r[4])
  end
end

script.on_init(init)
script.on_configuration_changed(init)
script.on_nth_tick(EVERY, tick)
