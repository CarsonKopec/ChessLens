using System;
using Microsoft.Gaming.XboxGameBar;
using Windows.UI.Core;
using Windows.UI.Text;
using Windows.UI.Xaml;
using Windows.UI.Xaml.Controls;
using Windows.UI.Xaml.Documents;
using Windows.UI.Xaml.Media;
using Windows.UI.Xaml.Navigation;

namespace ChessLensWidget
{
    /// <summary>ChessLens's analysis, re-read a few times a second from the file ChessLens keeps up to date.</summary>
    public sealed partial class WidgetPage : Page
    {
        private static readonly TimeSpan PollInterval = TimeSpan.FromMilliseconds(300);

        private readonly DispatcherTimer timer = new DispatcherTimer { Interval = PollInterval };
        private XboxGameBarWidget widget; // null when started from the Start menu rather than Game Bar
        private bool reading;

        public WidgetPage()
        {
            InitializeComponent();
            timer.Tick += OnTick;
        }

        protected override void OnNavigatedTo(NavigationEventArgs e)
        {
            widget = e.Parameter as XboxGameBarWidget;
            if (widget != null)
            {
                widget.RequestedOpacityChanged += OnRequestedOpacityChanged;
                BackgroundBrush.Opacity = widget.RequestedOpacity;
            }
            FairPlayText.Text = AnalysisSnapshot.DefaultFairPlay;
            timer.Start();
            OnTick(null, null);
        }

        protected override void OnNavigatedFrom(NavigationEventArgs e)
        {
            timer.Stop();
            if (widget != null)
            {
                widget.RequestedOpacityChanged -= OnRequestedOpacityChanged;
            }
        }

        private async void OnRequestedOpacityChanged(XboxGameBarWidget sender, object args)
        {
            // Game Bar raises this off the UI thread.
            await Dispatcher.RunAsync(CoreDispatcherPriority.Normal, () => BackgroundBrush.Opacity = sender.RequestedOpacity);
        }

        private async void OnTick(object sender, object e)
        {
            if (reading)
            {
                return;
            }
            reading = true;
            try
            {
                var snapshot = await AnalysisFeed.ReadAsync();
                if (snapshot != null)
                {
                    Show(snapshot);
                }
            }
            finally
            {
                reading = false;
            }
        }

        private void Show(AnalysisSnapshot s)
        {
            bool analysis = s.State == "analysis";
            Headline.Visibility = EvalBar.Visibility = LinesPanel.Visibility = analysis ? Visibility.Visible : Visibility.Collapsed;
            MessageText.Visibility = analysis ? Visibility.Collapsed : Visibility.Visible;
            GameText.Text = s.State == "stale" || s.State == "closed" ? "" : s.Game;
            FairPlayText.Text = s.FairPlay;
            if (!analysis)
            {
                MessageText.Text = s.Message;
                return;
            }

            EvalText.Text = s.Eval;
            BestText.Text = s.Lines.Count > 0 ? "Best: " + s.Lines[0].Move : "";
            TurnText.Text = s.Turn;
            DepthText.Text = s.Depth > 0 ? "depth " + s.Depth + (s.Final ? "" : "…") : "";
            double share = Math.Min(1, Math.Max(0, s.WhiteShare));
            WhiteColumn.Width = new GridLength(share, GridUnitType.Star);
            BlackColumn.Width = new GridLength(1 - share, GridUnitType.Star);

            var rows = new[] { Line1, Line2, Line3 };
            for (int i = 0; i < rows.Length; i++)
            {
                rows[i].Inlines.Clear();
                if (i >= s.Lines.Count)
                {
                    continue;
                }
                var line = s.Lines[i];
                rows[i].Inlines.Add(new Run { Text = line.Eval + "  ", FontWeight = FontWeights.SemiBold });
                rows[i].Inlines.Add(new Run { Text = line.Pv });
            }
        }
    }
}
