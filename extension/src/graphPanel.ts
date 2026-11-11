import * as vscode from 'vscode';
import { EngineClient, Report } from './engine';

/** Above this many files the graph opens at folder level instead of file level. */
const FILE_VIEW_LIMIT = 150;

type ViewState = { mode: 'files' | 'folders'; focus: string; depth: number };

type FromWebview =
	| { type: 'ready' }
	| { type: 'openFile'; path: string; line?: number }
	| { type: 'focus'; folder: string }
	| { type: 'view'; mode: 'files' | 'folders'; depth?: number };

/** The "Stratum" webview: Graphviz-rendered dependency graph plus a findings sidebar. */
export class GraphPanel {
	private static current: GraphPanel | undefined;
	private view: ViewState = { mode: 'files', focus: '', depth: 2 };
	private report: Report | undefined;
	private ready = false;

	static show(context: vscode.ExtensionContext, engine: EngineClient, report: Report): GraphPanel {
		if (GraphPanel.current) {
			GraphPanel.current.panel.reveal(vscode.ViewColumn.Beside);
		} else {
			GraphPanel.current = new GraphPanel(context, engine);
		}
		GraphPanel.current.setReport(report, true);
		return GraphPanel.current;
	}

	static refreshIfOpen(report: Report): void {
		GraphPanel.current?.setReport(report, false);
	}

	private constructor(
		private readonly context: vscode.ExtensionContext,
		private readonly engine: EngineClient,
		private readonly panel = vscode.window.createWebviewPanel('stratum.graph', 'Stratum', vscode.ViewColumn.Beside, {
			enableScripts: true,
			retainContextWhenHidden: true,
			localResourceRoots: [
				vscode.Uri.joinPath(context.extensionUri, 'media'),
				vscode.Uri.joinPath(context.extensionUri, 'node_modules', '@hpcc-js', 'wasm-graphviz', 'dist'),
			],
		}),
	) {
		panel.webview.html = this.html();
		panel.onDidDispose(() => (GraphPanel.current = undefined));
		panel.webview.onDidReceiveMessage((msg: FromWebview) => this.onMessage(msg));
	}

	private setReport(report: Report, resetView: boolean): void {
		this.report = report;
		if (resetView) {
			this.view = { mode: report.stats.files > FILE_VIEW_LIMIT ? 'folders' : 'files', focus: '', depth: 2 };
		}
		if (this.ready) {
			void this.render();
		}
	}

	private async onMessage(msg: FromWebview): Promise<void> {
		switch (msg.type) {
			case 'ready':
				this.ready = true;
				await this.render();
				break;
			case 'openFile':
				await this.openFile(msg.path, msg.line);
				break;
			case 'focus':
				this.view = { ...this.view, mode: 'files', focus: msg.folder };
				await this.render();
				break;
			case 'view':
				this.view = { ...this.view, mode: msg.mode, depth: msg.depth ?? this.view.depth, focus: msg.mode === 'folders' ? '' : this.view.focus };
				await this.render();
				break;
		}
	}

	private async render(): Promise<void> {
		if (!this.report) {
			return;
		}
		try {
			const dot = await this.engine.dot(this.view);
			await this.panel.webview.postMessage({ type: 'render', dot, view: this.view, report: summarize(this.report) });
		} catch (err) {
			vscode.window.showErrorMessage(`Stratum: ${(err as Error).message}`);
		}
	}

	private async openFile(rel: string, line?: number): Promise<void> {
		if (!this.report) {
			return;
		}
		const uri = vscode.Uri.joinPath(vscode.Uri.file(this.report.root), rel);
		const editor = await vscode.window.showTextDocument(uri, { viewColumn: vscode.ViewColumn.One, preview: true });
		if (line && line > 0) {
			const pos = new vscode.Position(line - 1, 0);
			editor.selection = new vscode.Selection(pos, pos);
			editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
		}
	}

	private html(): string {
		const webview = this.panel.webview;
		const media = (file: string) => webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'media', file));
		const graphviz = webview.asWebviewUri(
			vscode.Uri.joinPath(this.context.extensionUri, 'node_modules', '@hpcc-js', 'wasm-graphviz', 'dist', 'index.js'),
		);
		const nonce = Array.from({ length: 32 }, () => Math.floor(Math.random() * 36).toString(36)).join('');
		const csp = [
			"default-src 'none'",
			`style-src ${webview.cspSource} 'unsafe-inline'`,
			`img-src ${webview.cspSource} data:`,
			`script-src 'nonce-${nonce}' ${webview.cspSource} 'wasm-unsafe-eval'`,
		].join('; ');
		return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="${media('graph.css')}">
<title>Stratum</title>
</head>
<body>
<header id="toolbar">
  <div class="group">
    <button id="mode-folders" title="One box per folder">Folders</button>
    <button id="mode-files" title="One box per file">Files</button>
    <label id="depth-wrap">depth <select id="depth"><option>1</option><option selected>2</option><option>3</option><option>4</option></select></label>
  </div>
  <div id="crumbs"></div>
  <div class="group">
    <button id="zoom-out" title="Zoom out">−</button>
    <button id="zoom-fit" title="Fit to window">Fit</button>
    <button id="zoom-in" title="Zoom in">+</button>
  </div>
</header>
<main>
  <section id="canvas"><div id="graph"><p class="hint">Rendering…</p></div></section>
  <aside id="sidebar"></aside>
</main>
<script nonce="${nonce}">window.GRAPHVIZ_URL = ${JSON.stringify(graphviz.toString())};</script>
<script type="module" nonce="${nonce}" src="${media('graph.js')}"></script>
</body>
</html>`;
	}
}

/** Only what the sidebar needs; the full report can be megabytes on big repos. */
function summarize(r: Report) {
	return {
		stats: r.stats,
		cycles: r.cycles.slice(0, 50),
		unreachable: r.unreachable.slice(0, 200),
		duplicates: r.duplicates.slice(0, 50),
		complex: r.complex_functions.slice(0, 50),
		hubs: Object.entries(r.files)
			.sort((a, b) => b[1].fan_in - a[1].fan_in)
			.slice(0, 10)
			.map(([path, f]) => ({ path, fanIn: f.fan_in })),
	};
}
