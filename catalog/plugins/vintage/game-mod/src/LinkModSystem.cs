using System;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Vintagestory.API.Client;
using Vintagestory.API.Common;
using Vintagestory.API.Config;
using Vintagestory.API.MathTools;
using Vintagestory.GameContent;

namespace ClaudeDeckLink
{
    /// <summary>
    /// Міст до Noryx (лише клієнт).
    ///
    ///  * Стан персонажа — JSON по UDP на 127.0.0.1:47811 (плагін дека «Vintage Story» слухає там):
    ///    раз на секунду, а після команди лікування — частіше, поки не прийде відповідь сервера.
    ///  * Команди з дека — JSON по UDP на 127.0.0.1:47812 (лише з цього ж ПК):
    ///      {"cmd":"treat","zone":2,"type":"wound","treatment":"bandage","item":null,"target":0,"id":7}
    ///      {"cmd":"target","target":123456,"id":10}   — чиї травми слати (0 — свої; S&amp;R 0.6.2+)
    ///      {"cmd":"chat","text":"/home","id":8}        — команда гри («/…») або клієнта («.…»)
    ///      {"cmd":"hotbar","slot":3,"id":9}            — вибрати слот хотбара (1–10)
    ///    У кожній команді — "key": ключ із ModConfig/claudedecklink.json (інакше відповідь "key", команда не виконується).
    ///    Відповідь — у трьох наступних пакетах стану: "ack": {"id", "ok", "text"}. Не більше 20 команд на секунду.
    ///  * S&amp;R System (snrsystem) — через його публічні DeckStateJson() / DeckTreat(...) рефлексією:
    ///    без посилання на його збірку, тож міст працює й без S&amp;R (тоді лише здоров'я).
    /// Усе, що чіпає гру, — у головному потоці (тік і EnqueueMainThreadTask).
    /// </summary>
    public class LinkModSystem : ModSystem
    {
        public const int StatePort = 47811;
        public const int CommandPort = 47812;
        public const int Format = 1;         // формат пакета самого містка (стан S&R — усередині "snrs", зі своїм format)

        private ICoreClientAPI capi;
        private UdpClient sender;
        private UdpClient listener;
        private Thread listenThread;
        private volatile bool running;
        private long tickId;
        private bool busy;                   // іде лікування (є смужка прогресу) — слати частіше
        private long lastSentMs;
        private long fastUntilMs;            // після команди — слати частіше, щоб дек швидко побачив зміни
        private int lastSnrResult = -1;
        private JObject ack;                 // відповідь на останню команду — піде з кількома наступними пакетами
        private int ackLeft;                 // скільки пакетів ще везуть ack (дек усе одно відсіює за id)
        private string key;                  // спільний ключ із деком (ModConfig/claudedecklink.json): без нього команд не виконуємо
        private const int MaxCommandsPerSecond = 20;
        private bool warnedBig;

        // S&R System — рефлексією
        private object snr;
        private MethodInfo snrState, snrTreat, snrStateFor, snrTreatTarget;
        private long target;                 // чиї травми шле міст (0 — свої)
        private bool snrLooked;

        public override bool ShouldLoad(EnumAppSide forSide) => forSide == EnumAppSide.Client;

        public override void StartClientSide(ICoreClientAPI api)
        {
            capi = api;
            key = LoadKey(api);
            sender = new UdpClient(AddressFamily.InterNetwork);
            try
            {
                listener = new UdpClient(new IPEndPoint(IPAddress.Loopback, CommandPort));
                running = true;
                listenThread = new Thread(Listen) { IsBackground = true, Name = "claudedecklink" };
                listenThread.Start();
            }
            catch (Exception e)
            {
                // порт зайнятий (друга копія гри?) — стан шлемо, команд не приймаємо
                api.Logger.Warning("[Noryx Link] UDP {0} busy, commands disabled: {1}", CommandPort, e.Message);
            }
            tickId = api.Event.RegisterGameTickListener(OnTick, 250);
        }

        /// <summary>Ключ для команд: ModConfig/claudedecklink.json (немає — створюємо випадковий). Дек на цьому ж ПК читає
        /// той самий файл; програма без доступу до файлів гри (напр. у пісочниці Flatpak) команду не підробить.</summary>
        private static string LoadKey(ICoreClientAPI api)
        {
            LinkConfig cfg = null;
            try { cfg = api.LoadModConfig<LinkConfig>(ConfigFile); }
            catch (Exception e) { api.Logger.Warning("[Noryx Link] {0} unreadable, making a new key: {1}", ConfigFile, e.Message); }
            if (cfg == null || string.IsNullOrWhiteSpace(cfg.Key) || cfg.Key.Length < 16)
            {
                cfg = new LinkConfig { Key = Convert.ToHexString(System.Security.Cryptography.RandomNumberGenerator.GetBytes(16)).ToLowerInvariant() };
                api.StoreModConfig(cfg, ConfigFile);
                api.Logger.Notification("[Noryx Link] new key written to ModConfig/{0}", ConfigFile);
            }
            return cfg.Key.Trim();
        }

        public const string ConfigFile = "claudedecklink.json";

        public class LinkConfig
        {
            public string Key;
        }

        public override void Dispose()
        {
            running = false;
            if (capi != null && tickId != 0) capi.Event.UnregisterGameTickListener(tickId);
            try { listener?.Close(); } catch { }
            try { sender?.Close(); } catch { }
            base.Dispose();
        }

        // ================================================================ СТАН → ДЕК

        private void OnTick(float dt)
        {
            var me = capi.World?.Player?.Entity;
            if (me == null || sender == null) return;
            long now = capi.ElapsedMilliseconds;
            bool fast = now < fastUntilMs || busy;
            if (now - lastSentMs < (fast ? 250 : 1000)) return;
            lastSentMs = now;
            try
            {
                var json = BuildState(me);
                var progress = (json["snrs"] as JObject)?["progress"] as JObject;
                busy = progress != null && (progress["mine"]?.Type == JTokenType.Object || progress["onMe"]?.Type == JTokenType.Object);
                if (fast && SnrResultId(json) is int id && id != lastSnrResult)
                {
                    lastSnrResult = id;
                    fastUntilMs = 0;               // сервер відповів — далі звичайним темпом
                }
                byte[] data = Encoding.UTF8.GetBytes(json.ToString(Formatting.None));
                if (data.Length > 60000)
                {
                    if (!warnedBig) capi.Logger.Warning("[Noryx Link] state is {0} bytes, sending without S&R", data.Length);
                    warnedBig = true;
                    json.Remove("snrs");
                    data = Encoding.UTF8.GetBytes(json.ToString(Formatting.None));
                }
                sender.Send(data, data.Length, new IPEndPoint(IPAddress.Loopback, StatePort));
            }
            catch (Exception e)
            {
                capi.Logger.VerboseDebug("[Noryx Link] send: {0}", e.Message);
            }
        }

        private static int? SnrResultId(JObject json) => (json["snrs"] as JObject)?["result"]?["id"]?.Value<int>();

        private JObject BuildState(EntityPlayer me)
        {
            var world = capi.World;
            var cal = world.Calendar;
            var pos = me.Pos.AsBlockPos;
            var wa = me.WatchedAttributes;

            var o = new JObject
            {
                ["src"] = "claudedecklink",
                ["v"] = Format,
                ["lang"] = Lang.CurrentLocale ?? "en"
            };

            var health = wa.GetTreeAttribute("health");
            if (health != null)
            {
                o["hp"] = Math.Round(health.GetFloat("currenthealth"), 1);
                o["hpMax"] = Math.Round(health.GetFloat("maxhealth"), 1);
            }
            var hunger = wa.GetTreeAttribute("hunger");
            if (hunger != null)
            {
                float max = hunger.GetFloat("maxsaturation", 1500f);
                o["sat"] = Math.Round(hunger.GetFloat("currentsaturation"));
                o["satMax"] = Math.Round(max);
                o["food"] = new JObject
                {
                    ["fruit"] = Math.Round(hunger.GetFloat("fruitLevel")),
                    ["veg"] = Math.Round(hunger.GetFloat("vegetableLevel")),
                    ["grain"] = Math.Round(hunger.GetFloat("grainLevel")),
                    ["protein"] = Math.Round(hunger.GetFloat("proteinLevel")),
                    ["dairy"] = Math.Round(hunger.GetFloat("dairyLevel"))
                };
                o["foodMax"] = Math.Round(max);
            }
            var bodyTemp = wa.GetTreeAttribute("bodyTemp");
            if (bodyTemp != null) o["temp"] = Math.Round(bodyTemp.GetFloat("bodytemp", 37f), 1);
            o["stability"] = Math.Round(wa.GetDouble("temporalStability", 1.0), 3);

            // світ: повітря, опади, вітер
            var climate = world.BlockAccessor.GetClimateAt(pos, EnumGetClimateMode.NowValues);
            if (climate != null)
            {
                o["air"] = Math.Round(climate.Temperature, 1);
                o["rainfall"] = Math.Round(climate.Rainfall, 2);
            }
            var weather = capi.ModLoader.GetModSystem<WeatherSystemClient>();
            if (weather != null)
            {
                var ps = weather.GetPrecipitationState(me.Pos.XYZ);
                double level = ps?.Level ?? 0;
                string kind = "none";
                if (level > 0.05)
                {
                    var t = ps.Type;
                    if (t == EnumPrecipitationType.Auto) t = (climate?.Temperature ?? 10) < 0 ? EnumPrecipitationType.Snow : EnumPrecipitationType.Rain;
                    kind = t.ToString().ToLowerInvariant();
                }
                o["precip"] = kind;
                o["precipLevel"] = Math.Round(level, 2);
            }
            var wind = world.BlockAccessor.GetWindSpeedAt(pos);
            if (wind != null) o["wind"] = Math.Round(wind.Length(), 2);

            // час і дата
            float hour = cal.HourOfDay;
            int h = (int)hour, m = (int)((hour - h) * 60);
            o["time"] = $"{h:00}:{m:00}";
            o["hour"] = Math.Round(hour, 2);
            o["day"] = cal.DayOfYear + 1;
            o["month"] = cal.Month;
            o["year"] = cal.Year;
            o["season"] = cal.GetSeason(pos).ToString().ToLowerInvariant();
            o["date"] = cal.PrettyDate();

            // позиція — як показує гра (відносно точки появи світу)
            var local = pos.ToLocalPosition(capi);
            o["x"] = local.X; o["y"] = local.Y; o["z"] = local.Z;

            // темпоральний шторм
            var stab = capi.ModLoader.GetModSystem<SystemTemporalStability>();
            var storm = stab?.StormData;
            bool stormsOn = world.Config?.GetString("temporalStorms", null) != "off";
            if (storm != null)
            {
                o["storm"] = new JObject
                {
                    ["on"] = stormsOn,
                    ["active"] = storm.nowStormActive,
                    ["days"] = Math.Round(storm.nextStormTotalDays - cal.TotalDays, 3),
                    ["hoursPerDay"] = cal.HoursPerDay
                };
            }

            // S&R System — травми й лікування
            string snrJson = SnrState();
            if (snrJson != null)
            {
                try { o["snrs"] = JObject.Parse(snrJson); } catch { }
            }

            if (ack != null && ackLeft > 0) { o["ack"] = ack; ackLeft--; }
            return o;
        }

        // ================================================================ S&R SYSTEM

        private void FindSnr()
        {
            if (snrLooked) return;
            snrLooked = true;
            if (!capi.ModLoader.IsModEnabled("snrsystem")) return;
            snr = capi.ModLoader.GetModSystem("SnRSystem.SnrModSystem");
            var t = snr?.GetType();
            snrState = t?.GetMethod("DeckStateJson", BindingFlags.Public | BindingFlags.Instance, null, Type.EmptyTypes, null);
            snrTreat = t?.GetMethod("DeckTreat", BindingFlags.Public | BindingFlags.Instance, null,
                new[] { typeof(int), typeof(string), typeof(string), typeof(string) }, null);
            // 0.6.2+: інші гравці (чий стан і кого лікувати)
            snrStateFor = t?.GetMethod("DeckStateJsonFor", BindingFlags.Public | BindingFlags.Instance, null, new[] { typeof(long) }, null);
            snrTreatTarget = t?.GetMethod("DeckTreatTarget", BindingFlags.Public | BindingFlags.Instance, null,
                new[] { typeof(long), typeof(int), typeof(string), typeof(string), typeof(string) }, null);
            if (snr != null && (snrState == null || snrTreat == null))
                capi.Logger.Warning("[Noryx Link] S&R System is too old for the deck (needs 0.5.0+)");
            else if (snr != null)
                capi.Logger.Notification("[Noryx Link] S&R System found: own treatment yes, other players {0}",
                    snrStateFor != null && snrTreatTarget != null ? "yes" : "no (needs 0.6.2+)");
            else
                capi.Logger.Notification("[Noryx Link] S&R System not found: health without injuries");
        }

        private string SnrState()
        {
            FindSnr();
            if (snrState == null) return null;
            try
            {
                if (snrStateFor != null) return snrStateFor.Invoke(snr, new object[] { target }) as string;
                return snrState.Invoke(snr, null) as string;
            }
            catch (Exception e)
            {
                capi.Logger.VerboseDebug("[Noryx Link] S&R state: {0}", e.InnerException?.Message ?? e.Message);
                return null;
            }
        }

        // ================================================================ КОМАНДИ ← ДЕК

        private void Listen()
        {
            var from = new IPEndPoint(IPAddress.Any, 0);
            long windowStart = 0;
            int inWindow = 0;
            while (running)
            {
                byte[] data;
                try { data = listener.Receive(ref from); }
                catch { if (!running) return; continue; }
                if (!IPAddress.IsLoopback(from.Address)) continue;           // лише з цього ПК
                long now = Environment.TickCount64;                          // не більше MaxCommandsPerSecond: потік команд не забʼє чергу гри
                if (now - windowStart >= 1000) { windowStart = now; inWindow = 0; }
                if (++inWindow > MaxCommandsPerSecond) continue;
                JObject cmd;
                try { cmd = JObject.Parse(Encoding.UTF8.GetString(data)); }
                catch { continue; }
                capi.Event.EnqueueMainThreadTask(() => Handle(cmd), "claudedecklink-cmd");
            }
        }

        private void Handle(JObject cmd)
        {
            int id = 0;
            string what = "";
            string result;
            try
            {
                id = cmd["id"]?.Type == JTokenType.Integer ? cmd["id"].Value<int>() : 0;
                what = cmd["cmd"]?.Type == JTokenType.String ? cmd["cmd"].Value<string>() : "";
                string given = cmd["key"]?.Type == JTokenType.String ? cmd["key"].Value<string>() : "";
                if (!KeyOk(given))
                {
                    result = "key";                                          // дек підкаже: ключ із ModConfig не той
                    what = what.Length > 20 ? what.Substring(0, 20) : what;
                }
                else result = what switch
                {
                    "treat" => Treat(cmd),
                    "target" => Target(cmd["target"]?.Value<long>() ?? 0),
                    "chat" => Chat(cmd["text"]?.Value<string>()),
                    "hotbar" => Hotbar(cmd["slot"]?.Value<int>() ?? 0),
                    "ping" => "ok",
                    _ => "unknown command"
                };
            }
            catch (Exception e)
            {
                result = e.InnerException?.Message ?? e.Message;
            }
            ack = new JObject { ["id"] = id, ["cmd"] = what, ["ok"] = result == "ok", ["text"] = result == "ok" ? "" : result };
            ackLeft = 3;
            lastSentMs = 0;                     // відповідь — одразу, наступним тіком
        }

        private bool KeyOk(string given)
        {
            if (string.IsNullOrEmpty(key) || string.IsNullOrEmpty(given)) return false;
            return System.Security.Cryptography.CryptographicOperations.FixedTimeEquals(
                Encoding.UTF8.GetBytes(given), Encoding.UTF8.GetBytes(key));
        }

        private string Target(long id)
        {
            FindSnr();
            if (id != 0 && snrStateFor == null) return "S&R System 0.6.2+ needed to treat other players";
            target = id;
            return "ok";
        }

        private string Treat(JObject cmd)
        {
            FindSnr();
            if (snrTreat == null) return "S&R System (0.5.0+) not installed";
            long who = cmd["target"]?.Value<long>() ?? 0;
            int zone = cmd["zone"]?.Value<int>() ?? -1;
            string type = cmd["type"]?.Value<string>(), treatment = cmd["treatment"]?.Value<string>(), item = cmd["item"]?.Value<string>();
            string r;
            if (snrTreatTarget != null) r = snrTreatTarget.Invoke(snr, new object[] { who, zone, type, treatment, item }) as string;
            else if (who != 0) return "S&R System 0.6.2+ needed to treat other players";
            else r = snrTreat.Invoke(snr, new object[] { zone, type, treatment, item }) as string;
            if (r == "ok") fastUntilMs = capi.ElapsedMilliseconds + 4000;      // чекаємо відповідь сервера
            return r ?? "?";
        }

        private string Chat(string text)
        {
            text = (text ?? "").Trim();
            if (text.Length < 2 || text.Length > 300) return "empty";
            if (text.StartsWith(".")) { capi.TriggerChatMessage(text); return "ok"; }          // команда клієнта
            if (text.StartsWith("/")) { capi.SendChatMessage(text); return "ok"; }             // команда сервера
            return "only /commands or .commands";                                               // у чат не пишемо
        }

        private string Hotbar(int slot)
        {
            if (slot < 1 || slot > 10) return "slot 1-10";
            var inv = capi.World?.Player?.InventoryManager;
            if (inv == null) return "no player";
            inv.ActiveHotbarSlotNumber = slot - 1;
            return "ok";
        }
    }
}
