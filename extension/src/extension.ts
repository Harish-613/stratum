import * as vscode from 'vscode';
import * as path from 'path';
import { execFile } from 'child_process';

export function activate(context: vscode.ExtensionContext) {
	const command = vscode.commands.registerCommand('stratum.analyze', () => {
		const engineDir = path.join(context.extensionPath, '..', 'engine');
		const python = path.join(context.extensionPath, '..', '.venv', 'bin', 'python');

		execFile(python, ['-m', 'stratum_engine.cli', 'version'], { cwd: engineDir }, (error, stdout) => {
			if (error) {
				vscode.window.showErrorMessage(`Stratum engine failed: ${error.message}`);
				return;
			}
			const result = JSON.parse(stdout);
			vscode.window.showInformationMessage(`Stratum engine v${result.version} is running`);
		});
	});

	context.subscriptions.push(command);
}

export function deactivate() {}