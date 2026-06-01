import { __assign, __awaiter, __generator, __spreadArray } from "tslib";
import * as fs from "fs-extra";
import cloneDeep from "lodash.clonedeep";
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
        return __awaiter(this, void 0, void 0, function () {
            var buffer, nativeReplay;
            return __generator(this, function (_a) {
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
        return __assign({}, this.header);
    };
    Replay.prototype.getPlayerNames = function () {
        return __spreadArray([], this.playerNames);
    };
    Replay.prototype.getParameter = function () {
        return __assign({}, this.parameter);
    };
    Replay.prototype.getScriptName = function () {
        return this.scriptName;
    };
    Replay.prototype.getDecks = function () {
        return cloneDeep(this.decks);
    };
    return Replay;
}());
export { Replay };
//# sourceMappingURL=index.js.map