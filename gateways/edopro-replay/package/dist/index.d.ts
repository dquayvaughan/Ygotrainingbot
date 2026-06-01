/// <reference types="node" />
export interface ReplayHeader {
    id: number;
    version: number;
    flag: number;
    seed: number;
    dataSize: number;
    hash: number;
    props: Buffer;
}
export interface ReplayParameter {
    startLP: number;
    startHand: number;
    drawCount: number;
    duelFlags: number;
}
export interface Deck {
    main: number[];
    extra: number[];
}
export declare class Replay {
    static fromFile(path: string): Promise<Replay>;
    static fromBuffer(buffer: Buffer): Replay;
    private readonly native;
    private readonly header;
    private readonly parameter;
    private readonly decks;
    private readonly scriptName;
    private readonly playerNames;
    private constructor();
    getHeader(): {
        id: number;
        version: number;
        flag: number;
        seed: number;
        dataSize: number;
        hash: number;
        props: Buffer;
    };
    getPlayerNames(): string[];
    getParameter(): {
        startLP: number;
        startHand: number;
        drawCount: number;
        duelFlags: number;
    };
    getScriptName(): string;
    getDecks(): Deck[];
}
