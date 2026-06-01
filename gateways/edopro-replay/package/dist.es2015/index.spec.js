import { __awaiter, __generator } from "tslib";
import { Replay } from ".";
import * as fs from "fs";
import path from "path";
describe("yrp", function () {
    it("should retrieve replay header information", function () { return __awaiter(void 0, void 0, void 0, function () {
        var originalFileBuffer, replay;
        return __generator(this, function (_a) {
            originalFileBuffer = fs.readFileSync(path.join(process.cwd(), "./res/yrp-basic.yrp"));
            replay = Replay.fromBuffer(originalFileBuffer);
            expect(replay.getHeader()).toMatchSnapshot();
            return [2 /*return*/];
        });
    }); });
    it("should retrieve replay header information from file", function () { return __awaiter(void 0, void 0, void 0, function () {
        var replay;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0: return [4 /*yield*/, Replay.fromFile(path.join(process.cwd(), "./res/yrp-basic.yrp"))];
                case 1:
                    replay = _a.sent();
                    expect(replay.getHeader()).toMatchSnapshot();
                    return [2 /*return*/];
            }
        });
    }); });
    it("should retrieve player names", function () { return __awaiter(void 0, void 0, void 0, function () {
        var originalFileBuffer, replay;
        return __generator(this, function (_a) {
            originalFileBuffer = fs.readFileSync(path.join(process.cwd(), "./res/yrp-basic.yrp"));
            replay = Replay.fromBuffer(originalFileBuffer);
            expect(replay.getPlayerNames()).toMatchSnapshot();
            return [2 /*return*/];
        });
    }); });
    it("should retrieve replay parameters", function () { return __awaiter(void 0, void 0, void 0, function () {
        var originalFileBuffer, replay;
        return __generator(this, function (_a) {
            originalFileBuffer = fs.readFileSync(path.join(process.cwd(), "./res/yrp-basic.yrp"));
            replay = Replay.fromBuffer(originalFileBuffer);
            expect(replay.getParameter()).toMatchSnapshot();
            return [2 /*return*/];
        });
    }); });
    it("should retrieve replay script name", function () { return __awaiter(void 0, void 0, void 0, function () {
        var originalFileBuffer, replay;
        return __generator(this, function (_a) {
            originalFileBuffer = fs.readFileSync(path.join(process.cwd(), "./res/yrp-basic.yrp"));
            replay = Replay.fromBuffer(originalFileBuffer);
            expect(replay.getScriptName()).toMatchSnapshot();
            return [2 /*return*/];
        });
    }); });
    it("should parse replay decks", function () {
        var originalFileBuffer = fs.readFileSync(path.join(process.cwd(), "./res/yrp-basic.yrp"));
        var replay = Replay.fromBuffer(originalFileBuffer);
        expect(replay.getDecks()).toMatchSnapshot();
    });
});
//# sourceMappingURL=index.spec.js.map