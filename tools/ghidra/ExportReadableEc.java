// Recover EC/XRAM symbols and export all discovered 8051 functions as pseudo-C.
// @category EC Reverse Engineering

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

import ghidra.app.decompiler.*;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import ghidra.util.task.ConsoleTaskMonitor;

public class ExportReadableEc extends GhidraScript {
    private static class RegisterDef {
        long start, end;
        String name, access, meaning;
    }

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2 || args.length > 4) {
            throw new IllegalArgumentException(
                "usage: ExportReadableEc.java <ec_registers.tsv> <output-directory> " +
                "[entry,...|-] [function-symbols.tsv]");
        }

        List<RegisterDef> registers = readRegisters(Paths.get(args[0]));
        Path output = Paths.get(args[1]);
        Files.createDirectories(output);

        AddressSpace xram = currentProgram.getAddressFactory().getAddressSpace("EXTMEM");
        if (xram == null) {
            throw new IllegalStateException("8051 EXTMEM address space is unavailable");
        }
        ensureXramBlock(xram);
        installRegisterSymbols(xram, registers);
        if (args.length >= 3 && !args[2].equals("-")) installExplicitEntries(args[2]);
        else installEntrySymbols();
        discoverCalledFunctions();
        if (args.length == 4) installFunctionSymbols(Paths.get(args[3]));

        exportPseudoC(output.resolve(currentProgram.getName() + ".c"));
        exportRegisterIndex(output.resolve("ec-register-index.md"), xram, registers);
        println("Exported readable EC source to " + output.toAbsolutePath());
    }

    private List<RegisterDef> readRegisters(Path path) throws IOException {
        List<RegisterDef> result = new ArrayList<>();
        for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            if (line.isBlank() || line.startsWith("#")) continue;
            String[] fields = line.split("\\t", 4);
            if (fields.length != 4) throw new IOException("invalid register row: " + line);
            String[] range = fields[0].split("-", 2);
            RegisterDef item = new RegisterDef();
            item.start = Long.parseLong(range[0], 16);
            item.end = Long.parseLong(range.length == 2 ? range[1] : range[0], 16);
            item.name = fields[1];
            item.access = fields[2];
            item.meaning = fields[3];
            result.add(item);
        }
        return result;
    }

    private void ensureXramBlock(AddressSpace xram) throws Exception {
        Memory memory = currentProgram.getMemory();
        Address start = xram.getAddress(0);
        if (memory.getBlock(start) == null) {
            memory.createUninitializedBlock("EC_XRAM", start, 0x10000, false);
        }
    }

    private void installRegisterSymbols(AddressSpace xram, List<RegisterDef> registers)
            throws Exception {
        SymbolTable symbols = currentProgram.getSymbolTable();
        for (RegisterDef item : registers) {
            Address address = xram.getAddress(item.start);
            Symbol old = symbols.getPrimarySymbol(address);
            if (old != null && old.getSource() == SourceType.DEFAULT) old.delete();
            Symbol symbol = symbols.createLabel(
                address, "ec_" + item.name, SourceType.USER_DEFINED);
            symbol.setPrimary();
            setEOLComment(address, item.access + ": " + item.meaning);
        }
    }

    private void installEntrySymbols() throws Exception {
        String[] names = {"reset_vector", "int0_vector", "timer0_vector", "int1_vector",
                          "timer1_vector", "serial_vector", "timer2_vector"};
        long[] offsets = {0, 3, 0xb, 0x13, 0x1b, 0x23, 0x2b};
        SymbolTable symbols = currentProgram.getSymbolTable();
        AddressSpace code = currentProgram.getAddressFactory().getDefaultAddressSpace();
        for (int i = 0; i < offsets.length; i++) {
            Address address = code.getAddress(offsets[i]);
            symbols.createLabel(address, names[i], SourceType.USER_DEFINED);
            if (getFunctionAt(address) == null) createFunction(address, names[i]);
        }
    }

    private void installExplicitEntries(String entryList) throws Exception {
        AddressSpace code = currentProgram.getAddressFactory().getDefaultAddressSpace();
        SymbolTable symbols = currentProgram.getSymbolTable();
        for (String text : entryList.split(",")) {
            long offset = Long.parseLong(text.trim().replaceFirst("^0[xX]", ""), 16);
            Address address = code.getAddress(offset);
            disassemble(address);
            Symbol symbol = symbols.createLabel(address,
                String.format("bank_entry_%04x", offset), SourceType.USER_DEFINED);
            symbol.setPrimary();
            if (getFunctionAt(address) == null) createFunction(address, symbol.getName());
        }
    }

    private void discoverCalledFunctions() throws Exception {
        Listing listing = currentProgram.getListing();
        AddressSpace code = currentProgram.getAddressFactory().getDefaultAddressSpace();
        int created;
        do {
            created = 0;
            List<Address> targets = new ArrayList<>();
            InstructionIterator instructions = listing.getInstructions(true);
            while (instructions.hasNext()) {
                Instruction instruction = instructions.next();
                if (!instruction.getFlowType().isCall()) continue;
                for (Address target : instruction.getFlows()) {
                    if (target.getAddressSpace().equals(code) && getFunctionAt(target) == null) {
                        targets.add(target);
                    }
                }
            }
            for (Address target : targets) {
                if (getFunctionAt(target) != null) continue;
                disassemble(target);
                if (createFunction(target, null) != null) created++;
            }
        } while (created != 0 && !monitor.isCancelled());
        println("Discovered called functions: " +
                currentProgram.getFunctionManager().getFunctionCount());
    }

    private void installFunctionSymbols(Path path) throws Exception {
        AddressSpace code = currentProgram.getAddressFactory().getDefaultAddressSpace();
        SymbolTable symbols = currentProgram.getSymbolTable();
        int renamed = 0;
        for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            if (line.isBlank() || line.startsWith("#")) continue;
            String[] fields = line.split("\\t", 4);
            if (fields.length != 4) throw new IOException("invalid function-symbol row: " + line);
            long offset = Long.parseLong(fields[0], 16);
            Address address = code.getAddress(offset);
            Function function = getFunctionAt(address);
            if (function == null) {
                disassemble(address);
                function = createFunction(address, fields[1]);
            }
            if (function == null) continue;
            function.setName(fields[1], SourceType.USER_DEFINED);
            setPlateComment(address, fields[2] + " confidence: " + fields[3]);
            Symbol primary = symbols.getPrimarySymbol(address);
            if (primary != null) primary.setPrimary();
            renamed++;
        }
        println("Installed semantic function names: " + renamed);
    }

    private void exportPseudoC(Path path) throws IOException {
        DecompInterface decompiler = new DecompInterface();
        decompiler.toggleCCode(true);
        decompiler.toggleSyntaxTree(true);
        if (!decompiler.openProgram(currentProgram)) {
            throw new IOException("decompiler rejected program: " + decompiler.getLastMessage());
        }

        int exported = 0, failed = 0;
        try (BufferedWriter out = Files.newBufferedWriter(path, StandardCharsets.UTF_8)) {
            out.write("/* Generated from " + currentProgram.getName() +
                      ". Names prefixed ec_ refer to EC/XRAM, not code addresses. */\n\n");
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext() && !monitor.isCancelled()) {
                Function function = functions.next();
                DecompileResults result = decompiler.decompileFunction(function, 30, monitor);
                out.write("/* CODE:" + function.getEntryPoint() + " " + function.getName() + " */\n");
                if (result.decompileCompleted() && result.getDecompiledFunction() != null) {
                    out.write(result.getDecompiledFunction().getC());
                    exported++;
                } else {
                    out.write("/* decompilation failed: " + result.getErrorMessage() + " */\n");
                    failed++;
                }
                out.write("\n\n");
            }
        } finally {
            decompiler.dispose();
        }
        println("Pseudo-C functions: " + exported + " exported, " + failed + " failed");
    }

    private void exportRegisterIndex(Path path, AddressSpace xram,
                                     List<RegisterDef> registers) throws IOException {
        ReferenceManager refs = currentProgram.getReferenceManager();
        try (BufferedWriter out = Files.newBufferedWriter(path, StandardCharsets.UTF_8)) {
            out.write("# EC register index\n\n");
            out.write("Generated from the official GCU Service register names. References are " +
                      "static constant-address references only; computed DPTR accesses are absent.\n\n");
            out.write("| XRAM | Symbol | Access | Meaning | Static references |\n");
            out.write("|---:|---|:---:|---|---|\n");
            for (RegisterDef item : registers) {
                Address address = xram.getAddress(item.start);
                List<String> sources = new ArrayList<>();
                ReferenceIterator iterator = refs.getReferencesTo(address);
                while (iterator.hasNext()) {
                    Reference ref = iterator.next();
                    sources.add("`CODE:" + ref.getFromAddress() + "`");
                }
                String range = item.start == item.end ? String.format("0x%04X", item.start)
                    : String.format("0x%04X–0x%04X", item.start, item.end);
                out.write("| `" + range + "` | `ec_" + item.name + "` | " + item.access +
                          " | " + item.meaning.replace("|", "\\|") + " | " +
                          (sources.isEmpty() ? "—" : String.join(", ", sources)) + " |\n");
            }
        }
    }
}
