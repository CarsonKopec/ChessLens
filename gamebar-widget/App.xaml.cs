using System;
using Microsoft.Gaming.XboxGameBar;
using Windows.ApplicationModel.Activation;
using Windows.Foundation;
using Windows.UI.ViewManagement;
using Windows.UI.Xaml;
using Windows.UI.Xaml.Controls;
using Windows.UI.Xaml.Navigation;

namespace ChessLensWidget
{
    /// <summary>
    /// Shows the analysis page inside Game Bar (Win+G), or in an ordinary window when started from the
    /// Start menu, which is handy for checking that ChessLens's analysis is coming through.
    /// Activation follows https://learn.microsoft.com/gaming/game-bar/guide/app-activation
    /// </summary>
    sealed partial class App : Application
    {
        // Game Bar's channel to this widget; it must live as long as the widget does.
        private XboxGameBarWidget widget;

        public App()
        {
            InitializeComponent();
        }

        protected override void OnActivated(IActivatedEventArgs args)
        {
            if (args.Kind != ActivationKind.Protocol)
            {
                return;
            }
            var protocolArgs = args as IProtocolActivatedEventArgs;
            if (protocolArgs == null || protocolArgs.Uri.Scheme != "ms-gamebarwidget")
            {
                return;
            }
            var widgetArgs = args as XboxGameBarWidgetActivatedEventArgs;
            if (widgetArgs == null || !widgetArgs.IsLaunchActivation)
            {
                return; // repeat activations carry commands from other widgets; this one takes none
            }
            var frame = new Frame();
            frame.NavigationFailed += OnNavigationFailed;
            Window.Current.Content = frame;
            widget = new XboxGameBarWidget(widgetArgs, Window.Current.CoreWindow, frame);
            frame.Navigate(typeof(WidgetPage), widget);
            Window.Current.Activate();
        }

        protected override void OnLaunched(LaunchActivatedEventArgs e)
        {
            // Open at the widget's size in Game Bar (Package.appxmanifest), not a full-size window.
            ApplicationView.PreferredLaunchViewSize = new Size(380, 250);
            ApplicationView.PreferredLaunchWindowingMode = ApplicationViewWindowingMode.PreferredLaunchViewSize;
            var frame = Window.Current.Content as Frame;
            if (frame == null)
            {
                frame = new Frame();
                frame.NavigationFailed += OnNavigationFailed;
                Window.Current.Content = frame;
            }
            if (frame.Content == null)
            {
                frame.Navigate(typeof(WidgetPage), null);
            }
            Window.Current.Activate();
        }

        private void OnNavigationFailed(object sender, NavigationFailedEventArgs e)
        {
            throw new Exception("Failed to load page " + e.SourcePageType.FullName);
        }
    }
}
