using Mono.Cecil;
using Mono.Cecil.Cil;

const long WindowsTimerTicks = 156250;

if (args.Length < 2 || (args[0] != "--check" && args[0] != "--apply"))
{
    Console.Error.WriteLine("usage: windowsbase-timer --check|--apply <WindowsBase.dll> [output.dll]");
    return 2;
}

var mode = args[0];
var source = args[1];
var target = args.Length > 2 ? args[2] : source + ".patched";

using var module = ModuleDefinition.ReadModule(source, new ReaderParameters { InMemory = true });
var timer = module.GetType("System.Windows.Threading.DispatcherTimer")
    ?? throw new InvalidOperationException("DispatcherTimer not found");
var setter = timer.Properties.Single(p => p.Name == "Interval").SetMethod;
var body = setter.Body;

if (IsPatched(body))
{
    Console.WriteLine("already patched: Interval rounds up to multiples of 15.625 ms");
    return 0;
}

if (!StartsWithNegativeCheck(body))
{
    Console.Error.WriteLine("set_Interval does not start with the negative check this tool expects: not touching it");
    return 1;
}

if (mode == "--check")
{
    Console.WriteLine("original: Interval uses the value it is given; run with --apply to patch");
    return 1;
}

var timeSpan = setter.Parameters[0].ParameterType;
var getTicks = new MethodReference("get_Ticks", module.TypeSystem.Int64, timeSpan) { HasThis = true };
var fromTicks = new MethodReference("FromTicks", timeSpan, timeSpan);
fromTicks.Parameters.Add(new ParameterDefinition(module.TypeSystem.Int64));

var il = body.GetILProcessor();
var original = body.Instructions[0];
var value = setter.Parameters[0];
Instruction[] rounding =
[
    il.Create(OpCodes.Ldarga_S, value),
    il.Create(OpCodes.Call, getTicks),
    il.Create(OpCodes.Ldc_I8, 0L),
    il.Create(OpCodes.Ble_S, original),
    il.Create(OpCodes.Ldarga_S, value),
    il.Create(OpCodes.Call, getTicks),
    il.Create(OpCodes.Ldc_I8, WindowsTimerTicks - 1),
    il.Create(OpCodes.Add),
    il.Create(OpCodes.Ldc_I8, WindowsTimerTicks),
    il.Create(OpCodes.Div),
    il.Create(OpCodes.Ldc_I8, WindowsTimerTicks),
    il.Create(OpCodes.Mul),
    il.Create(OpCodes.Call, fromTicks),
    il.Create(OpCodes.Starg_S, value),
];
foreach (var instruction in rounding.Reverse())
{
    il.InsertBefore(body.Instructions[0], instruction);
}

module.Write(target);
Console.WriteLine($"patched: written to {target}");
return 0;

static bool IsPatched(MethodBody body) =>
    body.Instructions.Any(i => i.OpCode == OpCodes.Call && i.Operand is MethodReference m && m.Name == "FromTicks");

static bool StartsWithNegativeCheck(MethodBody body) =>
    body.Instructions.Take(8).Any(i => i.OpCode == OpCodes.Call && i.Operand is MethodReference m && m.Name == "get_TotalMilliseconds");
