"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
var tslib_1 = require("tslib");
var _1 = require(".");
var fs = tslib_1.__importStar(require("fs"));
var path_1 = tslib_1.__importDefault(require("path"));
describe("yrp", function () {
    it("should retrieve replay header information", function () { return tslib_1.__awaiter(void 0, void 0, void 0, function () {
        var originalFileBuffer, replay;
        return tslib_1.__generator(this, function (_a) {
            originalFileBuffer = fs.readFileSync(path_1.default.join(process.cwd(), "./res/yrp-basic.yrp"));
            replay = _1.Replay.fromBuffer(originalFileBuffer);
            expect(replay.getHeader()).toMatchSnapshot();
            return [2 /*return*/];
        });
    }); });
    it("should retrieve replay header information from file", function () { return tslib_1.__awaiter(void 0, void 0, void 0, function () {
        var replay;
        return tslib_1.__generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, _1.Replay.fromFile(path_1.default.join(process.cwd(), "./res/yrp-basic.yrp"))];
                case 1:
                    replay = _a.sent();
                    expect(replay.getHeader()).toMatchSnapshot();
                    return [2 /*return*/];
            }
        });
    }); });
    it("should retrieve player names", function () { return tslib_1.__awaiter(void 0, void 0, void 0, function () {
        var originalFileBuffer, replay;
        return tslib_1.__generator(this, function (_a) {
            originalFileBuffer = fs.readFileSync(path_1.default.join(process.cwd(), "./res/yrp-basic.yrp"));
            replay = _1.Replay.fromBuffer(originalFileBuffer);
            expect(replay.getPlayerNames()).toMatchSnapshot();
            return [2 /*return*/];
        });
    }); });
    it("should retrieve replay parameters", function () { return tslib_1.__awaiter(void 0, void 0, void 0, function () {
        var originalFileBuffer, replay;
        return tslib_1.__generator(this, function (_a) {
            originalFileBuffer = fs.readFileSync(path_1.default.join(process.cwd(), "./res/yrp-basic.yrp"));
            replay = _1.Replay.fromBuffer(originalFileBuffer);
            expect(replay.getParameter()).toMatchSnapshot();
            return [2 /*return*/];
        });
    }); });
    it("should retrieve replay script name", function () { return tslib_1.__awaiter(void 0, void 0, void 0, function () {
        var originalFileBuffer, replay;
        return tslib_1.__generator(this, function (_a) {
            originalFileBuffer = fs.readFileSync(path_1.default.join(process.cwd(), "./res/yrp-basic.yrp"));
            replay = _1.Replay.fromBuffer(originalFileBuffer);
            expect(replay.getScriptName()).toMatchSnapshot();
            return [2 /*return*/];
        });
    }); });
    it("should parse replay decks", function () {
        var originalFileBuffer = fs.readFileSync(path_1.default.join(process.cwd(), "./res/yrp-basic.yrp"));
        var replay = _1.Replay.fromBuffer(originalFileBuffer);
        expect(replay.getDecks()).toMatchSnapshot();
    });
});
//# sourceMappingURL=index.spec.js.map