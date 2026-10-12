// OCR con Vision (macOS) para leer las tablas de medidas, que en baruka.pe son imágenes.
// Uso:  ocr_vision <imagen>                    → JSON [{"t", "alt", "x", "y", "w", "h", "conf"}] de toda la imagen
//       ocr_vision <imagen> x,y,w,h [x,y,w,h…]  → JSON [[obs…], …] una lista por región (re-lectura de celdas)
// Coordenadas normalizadas 0–1 con origen ARRIBA a la izquierda (se invierte la y de Vision).
// "alt" son las otras hipótesis de Vision para el mismo texto (p. ej. «I1S» → «115»).
// Compilar: swiftc -O ocr_vision.swift -o ocr_vision   (extraer.py lo hace solo, en la caché)
import Foundation
import Vision
import AppKit

func leer(_ cg: CGImage, _ roi: CGRect?) throws -> [[String: Any]] {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = false          // no «corregir» cifras
    req.recognitionLanguages = ["es-ES", "en-US"]
    if let r = roi { req.regionOfInterest = r }
    try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    var out: [[String: Any]] = []
    for o in req.results ?? [] {
        let cands = o.topCandidates(5)
        guard let c = cands.first else { continue }
        var b = o.boundingBox
        if let r = roi {   // las cajas vienen relativas a la región: se pasan a la imagen entera
            b = CGRect(x: r.minX + b.minX * r.width, y: r.minY + b.minY * r.height, width: b.width * r.width, height: b.height * r.height)
        }
        out.append(["t": c.string, "alt": cands.dropFirst().map { $0.string }, "x": b.midX, "y": 1 - b.midY,
                    "w": b.width, "h": b.height, "conf": c.confidence])
    }
    return out
}

let args = CommandLine.arguments
guard args.count >= 2, let img = NSImage(contentsOfFile: args[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("no pude abrir la imagen\n".data(using: .utf8)!); exit(1)
}
var salida: Any
if args.count == 2 {
    salida = try leer(cg, nil)
} else {
    var listas: [[[String: Any]]] = []
    for a in args.dropFirst(2) {
        let v = a.split(separator: ",").compactMap { Double($0) }
        guard v.count == 4 else { listas.append([]); continue }
        let r = CGRect(x: max(0, v[0]), y: max(0, 1 - v[1] - v[3]), width: min(v[2], 1 - max(0, v[0])), height: v[3])
        listas.append((try? leer(cg, r)) ?? [])
    }
    salida = listas
}
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: salida, options: []))
