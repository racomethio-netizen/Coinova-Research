// Resolve the shared API contract (../packages/shared) with this app's own dependencies,
// so the app builds without the web app's node_modules (e.g. on EAS).
const path = require("node:path");
const { getDefaultConfig } = require("expo/metro-config");

const projectRoot = __dirname;
const sharedRoot = path.resolve(projectRoot, "../packages/shared");
const config = getDefaultConfig(projectRoot);
config.watchFolders = [sharedRoot];
config.resolver.nodeModulesPaths = [path.resolve(projectRoot, "node_modules")];
config.resolver.disableHierarchicalLookup = true;
module.exports = config;
