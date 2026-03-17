declare module "mpg123-decoder" {
  export class MPEGDecoder {
    ready: Promise<void>;
    decode(data: Uint8Array): {
      channelData: Float32Array[];
      sampleRate: number;
      samplesDecoded: number;
    };
    free(): void;
  }
}
