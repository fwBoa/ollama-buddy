// Ollama Buddy — enveloppe macOS native.
//
// Demarre le serveur local (ollama_buddy.py) s'il ne tourne pas deja, puis
// affiche le tableau de bord dans une fenetre WKWebView. Se comporte comme
// n'importe quelle app : icone dans le Dock, menu, Cmd-Q, Cmd-R.

import Cocoa
import WebKit

private let basePort = 11499
private let portAttempts = 4
private let appName = "Ollama Buddy"

/// Traductions. Comme dans les pages web, la recherche se fait par la chaine
/// anglaise : une cle absente laisse l'anglais, donc un texte neuf reste
/// lisible au lieu de disparaitre.
private let FRENCH: [String: String] = [
    "About %@": "À propos d'%@",
    "Show Window": "Afficher la fenêtre",
    "Open in Browser": "Ouvrir dans le navigateur",
    "Open Data Folder": "Ouvrir le dossier de données",
    "Hide %@": "Masquer %@",
    "Quit %@": "Quitter %@",
    "View": "Présentation",
    "Reload": "Recharger",
    "Refresh Data": "Actualiser les données",
    "Actual Size": "Taille réelle",
    "Zoom In": "Agrandir",
    "Zoom Out": "Réduire",
    "Enter Full Screen": "Plein écran",
    "Window": "Fenêtre",
    "Minimize": "Réduire",
    "quota not configured": "quota non configuré",
    "no figures published": "aucun chiffre publié",
    "no API key — open the dashboard to enter one":
        "aucune clé API — ouvre le tableau de bord pour la saisir",
    // Sentinelles envoyees par le serveur : voir cloud_usage cote Python.
    // Elles traversent `key_error` jusqu'a l'infobulle de la barre de menus.
    "key rejected": "clé refusée",
    "no key": "aucune clé",
    "Reading…": "Lecture…",
    "Starting the local server. This takes a moment on first launch.":
        "Démarrage du serveur local. Cela prend un instant au premier lancement.",
    "The embedded server is missing from the app.":
        "Le serveur embarqué est introuvable dans l'application.",
    "The server did not respond in time.":
        "Le serveur n'a pas répondu dans le délai imparti.",
    "Could not start the server: %@": "Impossible de lancer le serveur : %@",
    "Could not connect to the local server.\n\n%@":
        "Connexion au serveur local impossible.\n\n%@",
];

/// La langue de l'interface, resolue une fois pour toutes.
///
/// "auto" se resout ici et non cote serveur : lui seul sait dans quelle langue
/// la page s'affiche, et il ne connait pas la langue du Mac.
enum Lang {
    static var current = "en"

    static func resolve(_ pref: String) -> String {
        if pref == "fr" || pref == "en" { return pref }
        let first = Locale.preferredLanguages.first ?? "en"
        return first.lowercased().hasPrefix("fr") ? "fr" : "en"
    }

    static func t(_ english: String) -> String {
        current == "fr" ? (FRENCH[english] ?? english) : english
    }

    /// Le pourcentage colle a son symbole en anglais, detache en francais.
    static func percent(_ value: Double) -> String {
        let n = Int((value * 100).rounded())
        return current == "fr" ? "\(n) %" : "\(n)%"
    }
}
private let panelWidth: CGFloat = 300

/// Panneau flottant qui n'active pas l'app. Un NSPopover exige que
/// l'application puisse passer au premier plan : macOS le refuse quand une
/// autre app occupe l'ecran en plein ecran, et l'apercu ne s'ouvre alors
/// jamais. Un panneau non-activant s'affiche dans tous les cas.
final class PopoverPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate,
                         WKUIDelegate, WKScriptMessageHandler {

    private var window: NSWindow!
    private var webView: WKWebView!
    private var server: Process?
    private var launchedServer = false
    private var port = basePort

    // Barre de menus : un apercu consultable sans ouvrir la fenetre.
    private var statusItem: NSStatusItem!
    private var panel: PopoverPanel!
    private var miniWebView: WKWebView!
    private var statusTimer: Timer?
    private var miniLoadedAt: Date?
    private var miniBroken = false
    private var clickMonitor: Any?

    // MARK: - Cycle de vie

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Avant buildMenu : les menus sont construits une fois, et doivent
        // naitre dans la bonne langue.
        Lang.current = Lang.resolve("auto")
        buildMenu()
        buildWindow()
        buildStatusItem()
        start()
        statusTimer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { _ in
            self.refreshStatus()
        }
    }

    /// Fermer la fenetre ne quitte pas : l'app vit dans la barre de menus.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    /// Clic sur l'icone du Dock : on ramene la fenetre.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        showWindow()
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        // On n'arrete que le serveur qu'on a nous-meme demarre : si l'utilisateur
        // faisait deja tourner `python3 ollama_buddy.py`, on le laisse en vie.
        if launchedServer, let process = server, process.isRunning {
            process.terminate()
        }
    }

    // MARK: - Interface

    private func buildWindow() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        // Le handler sert de marqueur : la page en deduit qu'elle tourne dans
        // la fenetre native et reserve la place des feux tricolores.
        config.userContentController.add(self, name: "app")

        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        // Evite le flash blanc pendant le chargement.
        webView.setValue(false, forKey: "drawsBackground")
        if #available(macOS 12.0, *) {
            webView.underPageBackgroundColor = NSColor(red: 0.05, green: 0.05, blue: 0.05, alpha: 1)
        }

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1120, height: 880),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        // Par defaut, AppKit libere la fenetre des qu'on la ferme. La propriete
        // `window` pointerait alors sur une adresse liberee, et la rouvrir depuis
        // la barre de menus plantait en SIGSEGV — le cas normal, puisque fermer
        // la fenetre ne quitte pas l'app.
        window.isReleasedWhenClosed = false
        window.title = appName
        window.titlebarAppearsTransparent = true
        // Le titre systeme chevauchait celui de la page : la page porte deja
        // son nom, on masque celui de la fenetre.
        window.titleVisibility = .hidden
        window.isMovableByWindowBackground = true
        window.backgroundColor = NSColor(red: 0.05, green: 0.05, blue: 0.05, alpha: 1)
        window.minSize = NSSize(width: 680, height: 520)
        window.contentView = webView
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Reconstruit toute la barre de menus dans la langue courante.
    ///
    /// AppKit ne sait pas retraduire un menu en place : changer de langue
    /// passe donc par une reconstruction complete. C'est sans effet de bord,
    /// les raccourcis et les selecteurs restant identiques.
    private func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: String(format: Lang.t("About %@"), appName),
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: Lang.t("Show Window"),
                        action: #selector(showWindowAction), keyEquivalent: "1")
        appMenu.addItem(withTitle: Lang.t("Open in Browser"),
                        action: #selector(openInBrowser), keyEquivalent: "o")
        appMenu.addItem(withTitle: Lang.t("Open Data Folder"),
                        action: #selector(openDataFolder), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: String(format: Lang.t("Hide %@"), appName),
                        action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: String(format: Lang.t("Quit %@"), appName),
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let viewItem = NSMenuItem()
        main.addItem(viewItem)
        let viewMenu = NSMenu(title: Lang.t("View"))
        viewMenu.addItem(withTitle: Lang.t("Reload"), action: #selector(reload), keyEquivalent: "r")
        viewMenu.addItem(withTitle: Lang.t("Refresh Data"),
                         action: #selector(reloadFresh), keyEquivalent: "R")
        viewMenu.addItem(.separator())
        viewMenu.addItem(withTitle: Lang.t("Actual Size"), action: #selector(actualSize), keyEquivalent: "0")
        viewMenu.addItem(withTitle: Lang.t("Zoom In"), action: #selector(zoomIn), keyEquivalent: "+")
        viewMenu.addItem(withTitle: Lang.t("Zoom Out"), action: #selector(zoomOut), keyEquivalent: "-")
        viewMenu.addItem(.separator())
        viewMenu.addItem(withTitle: Lang.t("Enter Full Screen"), action: #selector(NSWindow.toggleFullScreen(_:)),
                         keyEquivalent: "f")
        viewItem.submenu = viewMenu

        let windowItem = NSMenuItem()
        main.addItem(windowItem)
        let windowMenu = NSMenu(title: Lang.t("Window"))
        windowMenu.addItem(withTitle: Lang.t("Minimize"), action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.zoom(_:)), keyEquivalent: "")
        windowItem.submenu = windowMenu
        NSApp.windowsMenu = windowMenu

        NSApp.mainMenu = main
    }

    // MARK: - Barre de menus

    private func buildStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        guard let button = statusItem.button else { return }
        button.target = self
        button.action = #selector(togglePanel(_:))
        button.imagePosition = .imageLeading
        updateStatusTitle(ratio: nil)

        let config = WKWebViewConfiguration()
        // L'apercu annonce sa hauteur reelle : sans cela le panneau garde la
        // taille demandee a la construction et laisse un vide sous le contenu.
        config.userContentController.add(self, name: "popoverSize")
        miniWebView = WKWebView(frame: NSRect(x: 0, y: 0, width: panelWidth, height: 260),
                                configuration: config)
        // Sans ce masque, la vue web garde la hauteur de construction quand le
        // panneau s'ajuste au contenu : le bas du panneau reste vide.
        miniWebView.autoresizingMask = [.width, .height]
        miniWebView.setValue(false, forKey: "drawsBackground")
        miniWebView.navigationDelegate = self
        if #available(macOS 12.0, *) {
            miniWebView.underPageBackgroundColor = .clear
        }

        // Le conteneur porte les coins arrondis et l'ombre : le panneau est
        // sans bordure.
        let container = NSView(frame: miniWebView.frame)
        container.wantsLayer = true
        container.layer?.cornerRadius = 12
        container.layer?.masksToBounds = true
        container.autoresizingMask = [.width, .height]
        container.addSubview(miniWebView)

        panel = PopoverPanel(contentRect: container.frame,
                             styleMask: [.borderless, .nonactivatingPanel],
                             backing: .buffered, defer: false)
        panel.contentView = container
        // Pas de isFloatingPanel : ce n'est pas un drapeau mais une affectation
        // de niveau — elle vaut .floating (3) — et AppKit la reapplique a
        // l'affichage. Le niveau voulu, pose juste apres, etait donc ecrase, et
        // l'apercu repassait derriere la fenetre active. Ce que la propriete
        // apporte par ailleurs — rester visible quand l'app n'est pas active —
        // est deja assure par hidesOnDeactivate plus bas.
        // .floating (3) et .statusBar (25) passent SOUS le contenu plein ecran.
        // Recommandation d'un ingenieur DTS d'Apple : niveau .screenSaver.
        panel.level = .screenSaver
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = true
        panel.isMovable = false
        panel.hidesOnDeactivate = false
        // Sans apparence explicite, la webview du panneau ne recoit pas le
        // theme du systeme et reste en clair.
        panel.appearance = NSApp.effectiveAppearance
        panel.collectionBehavior = panelBehavior()

        // Fermeture quand on clique ailleurs. On s'appuie sur la perte de focus
        // plutot que sur un moniteur global d'evenements : celui-ci exige
        // l'autorisation Accessibilite, celle-la non.
        NotificationCenter.default.addObserver(
            forName: NSWindow.didResignKeyNotification, object: panel, queue: .main
        ) { [weak self] _ in self?.hidePanel() }

        // Le systeme a bascule clair/sombre : le panneau doit suivre.
        DistributedNotificationCenter.default.addObserver(
            forName: Notification.Name("AppleInterfaceThemeChangedNotification"),
            object: nil, queue: .main
        ) { [weak self] _ in
            guard let self = self else { return }
            self.panel.appearance = NSApp.effectiveAppearance
            self.webView.appearance = NSApp.effectiveAppearance
        }
    }

    func userContentController(_ controller: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard message.name == "popoverSize",
              let value = message.body as? NSNumber else { return }
        let height = min(CGFloat(truncating: value), 620)
        guard height > 60 else { return }

        var frame = panel.frame
        guard abs(frame.height - height) > 0.5 else { return }
        let top = frame.maxY
        frame.size.height = height
        frame.origin.y = top - height      // le bord haut reste sous l'icone
        panel.setFrame(frame, display: true)
    }

    /// Le lama d'Ollama, en image gabarit : macOS l'inverse selon la barre.
    private func menuBarIcon() -> NSImage? {
        guard let url = Bundle.main.url(forResource: "ollama", withExtension: "png",
                                        subdirectory: "app/web"),
              let image = NSImage(contentsOf: url) else { return nil }
        image.size = NSSize(width: 13, height: 18)
        image.isTemplate = true
        return image
    }

    /// Le pourcentage. La couleur est reservee aux alertes : bleu partout
    /// serait du bruit permanent dans la barre de menus.
    private func updateStatusTitle(ratio: Double?, note: String = "") {
        guard let button = statusItem.button else { return }
        button.image = menuBarIcon() ?? dot(.secondaryLabelColor)
        button.imagePosition = .imageLeading

        let text: String
        let color: NSColor
        if let ratio = ratio {
            color = ratio >= 0.9 ? .systemRed : ratio >= 0.7 ? .systemOrange : .labelColor
            text = " " + Lang.percent(ratio)
        } else {
            color = .secondaryLabelColor
            text = " —"
        }
        button.attributedTitle = NSAttributedString(
            string: text,
            attributes: [.foregroundColor: color, .font: NSFont.systemFont(ofSize: 12)]
        )
        button.toolTip = ratio == nil
            ? "Ollama Buddy — \(note.isEmpty ? Lang.t("quota not configured") : note)"
            : (Lang.current == "fr"
               ? "Ollama Buddy — \(Lang.percent(ratio!)) du quota mensuel"
               : "Ollama Buddy — \(Lang.percent(ratio!)) of the monthly quota")
    }

    private func dot(_ color: NSColor) -> NSImage {
        let size = NSSize(width: 9, height: 9)
        let image = NSImage(size: size)
        image.lockFocus()
        color.setFill()
        NSBezierPath(ovalIn: NSRect(origin: .zero, size: size)).fill()
        image.unlockFocus()
        image.isTemplate = false
        return image
    }

    /// Comportement de Space du panneau : il suit l'utilisateur partout.
    ///
    /// Sans canJoinAllApplications + stationary, le panneau ne suit pas les
    /// autres Spaces et disparait derriere une app en plein ecran.
    /// canJoinAllApplications n'existe qu'a partir de macOS 13 : sur Monterey on
    /// garde le reste, quitte a ce que le panneau cede devant une app en plein
    /// ecran. L'Info.plist annonce bien macOS 12 comme minimum.
    private func panelBehavior() -> NSWindow.CollectionBehavior {
        var behavior: NSWindow.CollectionBehavior = [.canJoinAllSpaces,
                                                     .fullScreenAuxiliary,
                                                     .stationary, .ignoresCycle]
        if #available(macOS 13.0, *) {
            behavior.insert(.canJoinAllApplications)
        }
        return behavior
    }

    @objc private func togglePanel(_ sender: Any?) {
        if panel.isVisible {
            hidePanel()
            return
        }
        loadMini()
        positionPanel()
        // Le niveau est repose a chaque ouverture, et pas seulement a la
        // construction : AppKit le remet a celui de son choix dans certains cas
        // — panneau masque puis reaffiche, changement de Space, app active qui
        // change — et l'apercu repassait alors derriere la fenetre au premier
        // plan. Le poser ici, juste avant l'affichage, est ce qui compte.
        panel.level = .screenSaver
        panel.collectionBehavior = panelBehavior()
        // orderFrontRegardless : s'affiche sans que l'app ait a passer au
        // premier plan, ce qu'un popover ne savait pas faire.
        panel.orderFrontRegardless()
        panel.makeKey()
        clickMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]
        ) { [weak self] _ in self?.hidePanel() }
    }

    private func hidePanel() {
        panel.orderOut(nil)
        if let monitor = clickMonitor {
            NSEvent.removeMonitor(monitor)
            clickMonitor = nil
        }
    }

    /// Place le panneau sous l'icone, sans le laisser sortir de l'ecran.
    private func positionPanel() {
        guard let button = statusItem.button,
              let window = button.window,
              let screen = window.screen else { return }
        let anchor = window.convertToScreen(button.convert(button.bounds, to: nil))
        let size = panel.frame.size
        let visible = screen.visibleFrame
        let x = min(max(anchor.midX - size.width / 2, visible.minX + 8),
                    visible.maxX - size.width - 8)
        panel.setFrame(NSRect(x: x, y: anchor.minY - size.height - 6,
                              width: size.width, height: size.height), display: true)
    }

    /// La page reste chargee entre deux ouvertures : le flux SSE la tient a
    /// jour, et on evite le blanc d'un rechargement a chaque clic. On la
    /// recharge seulement si elle est vieille ou cassee.
    private func loadMini() {
        let stale = miniLoadedAt.map { Date().timeIntervalSince($0) > 60 } ?? true
        if miniWebView.url != nil && !stale && !miniBroken { return }
        guard let url = URL(string: "http://127.0.0.1:\(port)/mini") else { return }
        miniBroken = false
        miniLoadedAt = Date()
        miniWebView.load(URLRequest(url: url))
    }

    /// Lit le quota pour la barre de menus. Le serveur met la reponse en
    /// cache, cet appel toutes les 20 s ne coute donc rien.
    private func refreshStatus() {
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/usage?range=today") else { return }
        var request = URLRequest(url: url)
        request.timeoutInterval = 3
        URLSession.shared.dataTask(with: request) { payload, _, _ in
            guard let payload = payload,
                  let json = try? JSONSerialization.jsonObject(with: payload) as? [String: Any],
                  let quota = json["quota"] as? [String: Any] else { return }

            // Le pourcentage n'est publie que par ollama.com. Sans cle — ou avec
            // une cle refusee — `source` vaut "indisponible" et il n'y a rien a
            // afficher. On lit la part telle que l'API la donne, jamais un
            // rapport recalcule : la barre de menus ne peut plus diverger du
            // tableau de bord.
            let source = quota["source"] as? String
            let ratio = source == "api" ? quota["ratio"] as? Double : nil

            let note: String
            if ratio != nil {
                note = ""
            } else if (quota["key_set"] as? Bool) == true {
                // `key_error` vient du serveur : la sentinelle connue est
                // traduite, un code HTTP ou un nom d'exception reste tel quel.
                note = Lang.t(quota["key_error"] as? String ?? "no figures published")
            } else {
                note = Lang.t("no API key — open the dashboard to enter one")
            }

            // La langue voyage dans le meme payload que le quota : un
            // changement fait depuis le tableau de bord reconstruit donc les
            // menus ici, sans qu'aucun des deux n'ait a prevenir l'autre.
            let resolved = Lang.resolve(json["lang"] as? String ?? "auto")
            let needsMenu = resolved != Lang.current
            if needsMenu { Lang.current = resolved }

            DispatchQueue.main.async {
                if needsMenu { self.buildMenu() }
                self.updateStatusTitle(ratio: ratio, note: note)
            }
        }.resume()
    }

    private func showWindow() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if webView.url == nil { loadDashboard(showError: false) }
    }

    // MARK: - Démarrage du serveur

    private func start() {
        probe(port: port) { alive in
            if alive {
                self.loadDashboard(showError: false)
            } else {
                self.launchServer(at: self.port)
            }
        }
    }

    /// Interroge /health : le serveur repond-il deja sur ce port ?
    private func probe(port: Int, completion: @escaping (Bool) -> Void) {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/health")!)
        request.timeoutInterval = 1.2
        URLSession.shared.dataTask(with: request) { data, response, _ in
            let ok: Bool
            if let http = response as? HTTPURLResponse, http.statusCode == 200,
               let data = data, let text = String(data: data, encoding: .utf8) {
                ok = text.contains("ollama-buddy")
            } else {
                ok = false
            }
            DispatchQueue.main.async { completion(ok) }
        }.resume()
    }

    private func launchServer(at port: Int) {
        guard let script = Bundle.main.url(forResource: "ollama_buddy", withExtension: "py",
                                           subdirectory: "app") else {
            showFatal(Lang.t("The embedded server is missing from the app."))
            return
        }

        let dataDir = FileManager.default
            .homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/OllamaBuddy", isDirectory: true)
        try? FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [script.path, "--no-browser", "--port", String(port)]
        var env = ProcessInfo.processInfo.environment
        env["OLLAMA_BUDDY_DATA"] = dataDir.path
        process.environment = env
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice

        do {
            try process.run()
        } catch {
            showFatal(String(format: Lang.t("Could not start the server: %@"),
                             error.localizedDescription))
            return
        }

        server = process
        launchedServer = true
        webView.loadHTMLString(loadingPage(), baseURL: nil)
        waitForServer(attempts: 80)
    }

    private func waitForServer(attempts: Int) {
        guard attempts > 0 else {
            showFatal(Lang.t("The server did not respond in time."))
            return
        }
        // Le processus a pu mourir (port occupe, python absent) : on tente un autre port.
        if let process = server, !process.isRunning, port - basePort < portAttempts - 1 {
            port += 1
            launchedServer = false
            server = nil
            start()
            return
        }
        probe(port: port) { alive in
            if alive {
                self.loadDashboard(showError: false)
            } else {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                    self.waitForServer(attempts: attempts - 1)
                }
            }
        }
    }

    private func loadDashboard(showError: Bool) {
        guard let url = URL(string: "http://127.0.0.1:\(port)/") else { return }
        webView.load(URLRequest(url: url))
        // On precharge l'apercu : au premier clic, la page doit deja etre
        // prete, sinon le popover s'ouvre sur du vide.
        loadMini()
        refreshStatus()
    }

    private func showFatal(_ message: String) {
        webView.loadHTMLString(errorPage(message), baseURL: nil)
    }

    // MARK: - Actions

    @objc private func showWindowAction() { showWindow() }

    @objc private func reload() { webView.reload() }

    @objc private func reloadFresh() {
        // Redemande l'usage a ollama.com, puis recharge la page. Sans ce
        // passage, le cache d'une minute servirait la meme valeur.
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/refresh")!)
        request.httpMethod = "POST"
        URLSession.shared.dataTask(with: request) { _, _, _ in
            DispatchQueue.main.async { self.webView.reload() }
        }.resume()
    }

    @objc private func openInBrowser() {
        NSWorkspace.shared.open(URL(string: "http://127.0.0.1:\(port)/")!)
    }

    @objc private func openDataFolder() {
        let dir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/OllamaBuddy", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        NSWorkspace.shared.open(dir)
    }

    @objc private func actualSize() { setZoom(1.0) }
    @objc private func zoomIn() { setZoom(min(webView.pageZoom + 0.1, 2.0)) }
    @objc private func zoomOut() { setZoom(max(webView.pageZoom - 0.1, 0.6)) }

    private func setZoom(_ value: CGFloat) {
        webView.pageZoom = value
        webView.evaluateJavaScript("document.body.style.zoom = \(value)")
    }

    // MARK: - Navigation

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView === miniWebView {
            miniLoadedAt = Date()
            miniBroken = false
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        // Une navigation annulee par une autre n'est pas une erreur reelle.
        if (error as NSError).code == NSURLErrorCancelled { return }
        if webView === miniWebView { miniBroken = true }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code == NSURLErrorCancelled { return }
        if webView === miniWebView {
            // Le serveur a peut-etre redemarre : on retentera au prochain clic.
            miniBroken = true
            return
        }
        showFatal(String(format: Lang.t("Could not connect to the local server.\n\n%@"),
                         error.localizedDescription))
    }

    // Les liens externes s'ouvrent dans le navigateur, pas dans la fenetre.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else {
            decisionHandler(.allow)
            return
        }

        // Schema interne : l'apercu de la barre de menus demande la fenetre.
        if url.scheme == "ollama-buddy" {
            if url.host == "open" {
                hidePanel()
                showWindow()
            }
            decisionHandler(.cancel)
            return
        }

        if let host = url.host, host != "127.0.0.1" && host != "localhost",
           action.navigationType == .linkActivated {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    // MARK: - Pages de statut

    private func shell(_ title: String, _ body: String, spinner: Bool) -> String {
        """
        <!doctype html><html lang="fr"><head><meta charset="utf-8"><style>
          :root { color-scheme: dark; }
          body { margin:0; height:100vh; display:flex; align-items:center; justify-content:center;
                 background:#0d0d0d; color:#c3c2b7;
                 font:14px/1.6 system-ui,-apple-system,sans-serif; text-align:center; }
          .box { max-width: 420px; padding: 0 32px; }
          h1 { font-size:17px; font-weight:600; color:#fff; margin:0 0 10px; }
          p { margin:0; }
          .spin { width:22px; height:22px; margin:0 auto 22px; border-radius:50%;
                  border:2px solid #2c2c2a; border-top-color:#3987e5;
                  animation:r 900ms linear infinite; }
          @keyframes r { to { transform: rotate(360deg); } }
          @media (prefers-reduced-motion: reduce) { .spin { animation-duration: 3s; } }
        </style></head><body><div class="box">
          \(spinner ? "<div class=\"spin\"></div>" : "")
          <h1>\(title)</h1><p>\(body)</p>
        </div></body></html>
        """
    }

    private func loadingPage() -> String {
        shell(Lang.t("Reading…"),
              Lang.t("Starting the local server. This takes a moment on first launch."),
              spinner: true)
    }

    private func errorPage(_ message: String) -> String {
        shell("Ollama Buddy", message.replacingOccurrences(of: "\n", with: "<br>"), spinner: false)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
