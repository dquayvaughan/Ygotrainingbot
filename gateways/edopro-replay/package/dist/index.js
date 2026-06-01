"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Replay = void 0;
var tslib_1 = require("tslib");
var fs = tslib_1.__importStar(require("fs-extra"));
var lodash_clonedeep_1 = tslib_1.__importDefault(require("lodash.clonedeep"));
// eslint-disable-next-line @typescript-eslint/no-var-requires
var addon = require("bindings")("yrp.node");
var NativeReplay = addon.Replay;
var Replay = /** @class */ (function () {
    function Replay(nativeReplay) {
        this.native = nativeReplay;
        this.header = this.native.getHeaderInformation();
        this.parameter = this.native.getParameters();
        this.playerNames = this.native.getPlayerNames();
        this.scriptName = this.native.getScriptName();
        this.decks = this.native.getDecks();
    }
    Replay.fromFile = function (path) {
        return tslib_1.__awaiter(this, void 0, void 0, function () {
            var buffer, nativeReplay;
            return tslib_1.__generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, fs.readFile(path)];
                    case 1:
                        buffer = _a.sent();
                        nativeReplay = new NativeReplay(buffer);
                        return [2 /*return*/, new Replay(nativeReplay)];
                }
            });
        });
    };
    Replay.fromBuffer = function (buffer) {
        return new Replay(new NativeReplay(buffer));
    };
    Replay.prototype.getHeader = function () {
        return tslib_1.__assign({}, this.header);
    };
    Replay.prototype.getPlayerNames = function () {
        return tslib_1.__spreadArray([], this.playerNames);
    };
    Replay.prototype.getParameter = function () {
        return tslib_1.__assign({}, this.parameter);
    };
    Replay.prototype.getScriptName = function () {
        return this.scriptName;
    };
    Replay.prototype.getDecks = function () {
        return lodash_clonedeep_1.default(this.decks);
    };
    return Replay;
}());
exports.Replay = Replay;
//# sourceMappingURL=index.js.map