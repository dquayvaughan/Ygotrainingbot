// Starter template — add to ProjectIgnis/windbot Game/AI/Decks/ and implement
// prompt handlers that POST legal_actions to ygotrain edopro-bot-serve (port 8765).
//
// Build inside the WindBot solution; this file alone is not compiled by Ygotrainingbot.

using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using WindBot;
using WindBot.Game;
using WindBot.Game.AI;

namespace WindBot.Game.AI.Decks
{
    [Deck("Ygotrainingbot", "AI_Ygotrainingbot")]
    public class YgotrainingbotExecutor : DefaultExecutor
    {
        private static readonly HttpClient Http = new HttpClient();
        private static readonly Uri ServerBase = new Uri("http://127.0.0.1:8765/");
        private string _sessionId;

        public YgotrainingbotExecutor(GameAI ai, Duel duel)
            : base(ai, duel)
        {
            _sessionId = StartSession();
        }

        private string StartSession()
        {
            var payload = new Dictionary<string, object>
            {
                ["human_player"] = "you",
                ["bot_player"] = Bot.Username ?? "bot",
                ["format"] = "edopro-live",
            };
            var json = Post("/v1/start", payload);
            if (json.TryGetProperty("session_id", out var id))
                return id.GetString();
            return Guid.NewGuid().ToString();
        }

        // TODO: override WindBot selection hooks and call Decide() with legal_actions.
        // Example stub — replace with real ClientCard → action_id mapping.
        private int Decide(string summary, IList<(string id, string label)> options)
        {
            var actions = new List<object>();
            foreach (var opt in options)
            {
                actions.Add(new Dictionary<string, string>
                {
                    ["action_id"] = opt.id,
                    ["label"] = opt.label,
                });
            }
            var payload = new Dictionary<string, object>
            {
                ["session_id"] = _sessionId,
                ["summary"] = summary,
                ["duel_turn"] = Duel.Turn,
                ["decision_index"] = Duel.Turn,
                ["active_player"] = Bot.Username,
                ["legal_actions"] = actions,
            };
            var json = Post("/v1/decide", payload);
            if (json.TryGetProperty("action_id", out var actionId))
            {
                var chosen = actionId.GetString();
                for (int i = 0; i < options.Count; i++)
                {
                    if (options[i].id == chosen)
                        return i;
                }
            }
            return 0;
        }

        private JsonElement Post(string path, object body)
        {
            var content = new StringContent(
                JsonSerializer.Serialize(body),
                Encoding.UTF8,
                "application/json");
            var response = Http.PostAsync(new Uri(ServerBase, path.TrimStart('/')), content).Result;
            var text = response.Content.ReadAsStringAsync().Result;
            using var doc = JsonDocument.Parse(text);
            return doc.RootElement.Clone();
        }
    }
}
