using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using Windows.Data.Json;
using Windows.Storage;

namespace ChessLensWidget
{
    /// <summary>One reading of the analysis file ChessLens keeps in this app's LocalState folder.</summary>
    public sealed class AnalysisSnapshot
    {
        public const string DefaultFairPlay = "Engine help only where it's allowed: bots, puzzles, study, finished games.";

        // "analysis", "thinking", "message", "closed" (from ChessLens), or "stale" / "missing" (worked out here).
        public string State = "missing";
        public string Message = "";
        public string Turn = "";
        public string Eval = "";
        public double WhiteShare = 0.5;
        public int Depth;
        public bool Final;
        public string Game = "";
        public string FairPlay = DefaultFairPlay;
        public List<Line> Lines = new List<Line>();

        public sealed class Line
        {
            public string Move = "";
            public string Eval = "";
            public string Pv = "";
        }
    }

    /// <summary>
    /// Reads what ChessLens writes (src/chesslens/gamebar.py). A Game Bar widget is a UWP app: it can't
    /// open other programs' files or, by default, reach them over the loopback, so ChessLens writes into
    /// this app's own folder instead.
    /// </summary>
    public static class AnalysisFeed
    {
        public const string FileName = "analysis.json";
        private const int SupportedFormat = 1;
        private const double MissedHeartbeats = 3; // ChessLens counts as gone after this many

        /// <summary>The latest snapshot, or null when the file couldn't be read this time (ChessLens was
        /// replacing it at that moment); try again at the next tick.</summary>
        public static async Task<AnalysisSnapshot> ReadAsync()
        {
            string text;
            try
            {
                var file = await ApplicationData.Current.LocalFolder.TryGetItemAsync(FileName) as StorageFile;
                if (file == null)
                {
                    return new AnalysisSnapshot
                    {
                        State = "missing",
                        Message = "Waiting for ChessLens. Start it and turn on Engine analysis.",
                    };
                }
                text = await FileIO.ReadTextAsync(file);
            }
            catch (Exception)
            {
                return null;
            }
            return Parse(text, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0);
        }

        public static AnalysisSnapshot Parse(string text, double now)
        {
            JsonObject doc;
            if (!JsonObject.TryParse(text, out doc))
            {
                return null;
            }
            try
            {
                if (doc.GetNamedNumber("format", 0) > SupportedFormat)
                {
                    return new AnalysisSnapshot { State = "message", Message = "ChessLens is newer than this widget: update the widget." };
                }
                var snapshot = new AnalysisSnapshot
                {
                    State = doc.GetNamedString("state", "message"),
                    Message = doc.GetNamedString("message", ""),
                    Turn = doc.GetNamedString("turn", ""),
                    Eval = doc.GetNamedString("eval", ""),
                    WhiteShare = doc.GetNamedNumber("white_share", 0.5),
                    Depth = (int)doc.GetNamedNumber("depth", 0),
                    Final = doc.GetNamedBoolean("final", false),
                    Game = doc.GetNamedString("game", ""),
                    FairPlay = doc.GetNamedString("fair_play", AnalysisSnapshot.DefaultFairPlay),
                };
                foreach (var value in doc.GetNamedArray("lines", new JsonArray()))
                {
                    var line = value.GetObject();
                    snapshot.Lines.Add(new AnalysisSnapshot.Line
                    {
                        Move = line.GetNamedString("move", ""),
                        Eval = line.GetNamedString("eval", ""),
                        Pv = line.GetNamedString("pv", ""),
                    });
                }
                double writtenAt = doc.GetNamedNumber("written_at", now);
                double heartbeat = doc.GetNamedNumber("heartbeat_s", 5);
                if (snapshot.State != "closed" && now - writtenAt > MissedHeartbeats * heartbeat)
                {
                    snapshot.State = "stale";
                    snapshot.Message = "ChessLens isn't running. Start it and turn on Engine analysis.";
                }
                return snapshot;
            }
            catch (Exception)
            {
                return null; // a field of an unexpected type: keep showing the last good reading
            }
        }
    }
}
